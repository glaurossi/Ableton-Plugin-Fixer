#!/usr/bin/env python3
"""
Ableton Plugin Fixer

This tool scans Ableton Live projects for missing VST2 plugins and replaces them
with available VST3 equivalents, preserving all parameter data.
"""

import gzip
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DebugLogger:
    """
    Provides logging methods for different verbosity levels.
    """

    # debug level constants
    DEBUG_LEVEL_MINIMAL = 1
    DEBUG_LEVEL_ADVANCED = 2
    DEBUG_LEVEL_NERDY = 3

    def __init__(self, log_file: str, debug_level: int = 1):
        """
        Initialize logger.
        """
        self.log_file = log_file
        self.debug_level = debug_level
        self.log_fp = None
        self.session_start = None

    def start(self, session_start: datetime):
        """
        Start logging session.
        """
        self.session_start = session_start
        self.log_fp = open(self.log_file, "a", encoding="utf-8", buffering=1)
        self.log("")
        self.log(f"LOG - Date: {session_start.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log("=" * 35)

        # log debug level indicator
        if self.debug_level >= self.DEBUG_LEVEL_NERDY:
            self.log(f"Debug Level: {self.debug_level} (Nerdy)")
        elif self.debug_level >= self.DEBUG_LEVEL_ADVANCED:
            self.log(f"Debug Level: {self.debug_level} (Advanced)")
        else:
            self.log(f"Debug Level: {self.debug_level} (Minimal)")

    def stop(self):
        """Stop logging session and close file."""
        if self.log_fp:
            session_end = datetime.now()
            duration = session_end - self.session_start if self.session_start else None

            self.log("=" * 80)
            self.log("\n\n")

            self.log_fp.close()
            self.log_fp = None

    def log(self, message: str, level: int = 1):
        """
        Log a message if current debug level >= required level.
        """
        if self.log_fp and self.debug_level >= level:
            self.log_fp.write(f"{message}\n")

    def log_error(self, message: str):
        """
        Log an error message (always logged regardless of level).
        """
        if self.log_fp:
            self.log_fp.write(f"ERROR: {message}\n")

    def log_info(self, message: str, level: int = 1):
        """
        Log informational message at specified level.
        """
        self.log(f" INFO: {message}", level)

    def log_debug(self, message: str):
        """
        Log debug message (level 3 only).
        """
        self.log(f"{message}", self.DEBUG_LEVEL_NERDY)

    def log_change(self, change_type: str, details: str):
        """
        Log a file change (level 2+).
        """
        self.log(f"{details}", self.DEBUG_LEVEL_ADVANCED)


class XmlProcessor:
    """
    Base class for common XML processing operations.
    """

    def _parse_project_xml(
        self, project_path: str
    ) -> Tuple[ET.Element, bool, Optional[str]]:
        """
        Parse project XML, handling both gzipped .als and plain XML files.
        Returns: (root_element, is_gzipped, original_content)
        """
        try:
            with gzip.open(project_path, "rt", encoding="utf-8") as f:
                content = f.read()
            root = ET.fromstring(content)
            return root, True, content
        except (gzip.BadGzipFile, UnicodeDecodeError):
            # fallback for extracted XML files
            tree = ET.parse(project_path)
            root = tree.getroot()
            return root, False, None

    def _safe_execute(self, operation, error_msg: str, return_value=None):
        """
        Safely execute an operation with consistent error handling.
        """
        try:
            return operation()
        except Exception as e:
            print(f"{error_msg}: {e}")

            if hasattr(self, "logger") and self.logger:
                self.logger.log_error(f"{error_msg}: {e}")
            elif hasattr(self, "log_fp") and self.log_fp:
                self.log_fp.write(f"ERROR: {error_msg}: {e}\n")
            return return_value


@dataclass
class PluginInfo:
    """
    Represents a plugin found on the system (from Live's database).
    """

    name: str
    unique_id: str
    path: str
    plugin_type: str
    version: str = ""
    manufacturer: str = ""


@dataclass
class ProjectPlugin:
    """
    Represents a plugin used in a Live project file.
    """

    name: str
    unique_id: str
    path: str
    plugin_type: str
    parameter_data: str
    xml_element: ET.Element
    is_missing: bool = False


class PluginScanner:
    """
    Loads plugin information from Live's internal database.
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[DebugLogger] = None):
        """
        Initialize the scanner with configuration settings.
        """
        self.plugins: Dict[str, PluginInfo] = {}
        self.config = config
        self.logger = logger
        self.db_path = self._find_ableton_database()

    def scan_plugins(self) -> Dict[str, PluginInfo]:
        """
        Load all installed plugins from Live's database.
        """
        print("\nLoading plugins from Ableton database...")

        if self.logger:
            self.logger.log_debug("")
            self.logger.log_debug("===== PLUGIN SCANNING PHASE =====")
            self.logger.log_debug(f"Searching for Ableton Live database...")

        if not self.db_path or not os.path.exists(self.db_path):
            print(f"Error: Live database not found at: {self.db_path}")
            print(
                "Please ensure Ableton Live is installed and has been run at least once."
            )
            if self.logger:
                self.logger.log_debug(f"Database not found: {self.db_path}")
            return {}

        if self.logger:
            self.logger.log("")
            self.logger.log_debug(f"Database path: {os.path.normpath(self.db_path)}")
            self.logger.log_debug("Database connection: SUCCESS")

        try:
            self._load_plugins_from_database()

            if self.logger:
                self.logger.log("")
                self.logger.log_debug(f"Total plugins loaded: {len(self.plugins)}")

            print(f"\nFound {len(self.plugins)} plugins")
            return self.plugins

        except Exception as e:
            print(f"Error loading from database: {e}")
            print("Please check your Live installation.")
            if self.logger:
                self.logger.log_debug(f"Error loading from database: {e}")
            return {}

    def _find_ableton_database(self) -> Optional[str]:
        """
        Find Live's plugin database file using config or OS defaults.
        """
        db_path = self.config.get("database", {}).get("path")
        if db_path:
            return db_path if os.path.exists(db_path) else None

        db_paths = {
            "nt": os.path.expandvars(
                "%LOCALAPPDATA%\\Ableton\\Live Database"
            ),
            "posix": os.path.expanduser(
                "~/Library/Application Support/Ableton/Live Database/Live-plugins-1.db"
            ),
        }

        if os.name == "posix" and os.path.exists(db_paths["posix"]):
            return db_paths["posix"]
        elif os.name == "nt" and os.path.exists(db_paths["nt"]):
            db_filename = next(Path(db_paths["nt"]).glob("*.db"), None)
            if (db_filename != None):
                return os.path.join(db_paths["nt"], db_filename)

        return None

    def _load_plugins_from_database(self):
        """
        Load all plugins from Live's database.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # get list of all tables in the database
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {t[0] for t in cursor.fetchall()}

            if self.logger:
                self.logger.log_debug(
                    f"Database schema: {'modern' if 'plugins' in tables else 'legacy'}"
                )

            # use modern schema if 'plugins' table exists
            if "plugins" in tables:
                self._load_modern_schema(cursor, tables)
            else:
                # legacy schemas not supported
                print(
                    "Error: Legacy database schema detected. Please ensure you're using a supported version of Live."
                )
                if self.logger:
                    self.logger.log_debug(
                        "Legacy database schema not supported - requires modern schema with 'plugins' table"
                    )
                raise Exception(
                    "Unsupported database schema: legacy schema not supported"
                )

    def _load_modern_schema(self, cursor, tables):
        """
        Load plugins using modern Live database schema.
        """
        # get column information for the plugins table
        cursor.execute("PRAGMA table_info(plugins);")
        plugins_cols = [col[1] for col in cursor.fetchall()]

        # get module columns if available
        module_cols = []
        if "plugin_modules" in tables:
            cursor.execute("PRAGMA table_info(plugin_modules);")
            module_cols = [col[1] for col in cursor.fetchall()]

        # map expected column names to actual database columns
        cols = {
            "name": "name" if "name" in plugins_cols else None,
            "vendor": "vendor" if "vendor" in plugins_cols else None,
            "dev_id": "dev_identifier" if "dev_identifier" in plugins_cols else None,
            "module_id": "module_id" if "module_id" in plugins_cols else None,
            "version": "version" if "version" in plugins_cols else None,
        }

        # find path column in plugin_modules table
        path_candidates = [
            c for c in ["path", "location", "file_path"] if c in module_cols
        ]

        if cols["name"] and cols["dev_id"]:
            # build query based on available tables and columns
            if "plugin_modules" in tables and cols["module_id"] and path_candidates:
                path_col = path_candidates[0]
                query = f"SELECT p.{cols['dev_id']}, p.{cols['name']}, IFNULL(p.{cols['vendor']}, ''), p.{cols['module_id']}, m.{path_col}, IFNULL(p.{cols['version']}, '') FROM plugins p LEFT JOIN plugin_modules m ON p.{cols['module_id']}=m.module_id;"
            else:
                query = f"SELECT p.{cols['dev_id']}, p.{cols['name']}, IFNULL(p.{cols['vendor']}, ''), p.{cols['module_id']}, '', IFNULL(p.{cols['version']}, '') FROM plugins p;"

            # execute query and parse results
            for row in cursor.execute(query):
                try:
                    dev_identifier, name, vendor, module_id, path, version = row
                    plugin_info = PluginInfo(
                        name=str(name or ""),
                        unique_id=str(dev_identifier or ""),
                        path=str(path or ""),
                        plugin_type="vst3",
                        version=str(version or ""),
                        manufacturer=str(vendor or ""),
                    )
                    # use unique key to avoid duplicates
                    self.plugins[f"{plugin_info.unique_id}_{plugin_info.name}"] = (
                        plugin_info
                    )
                except Exception as e:
                    print(f"Error parsing plugin row: {e}")
        else:
            # this is a silly error message -- it's here just to remind me to update the code when the schema changes
            print(
                "Error: Unknown database schema. Please ensure you're using a supported version of Live."
            )
            raise Exception("Unsupported database schema")


class ProjectAnalyzer(XmlProcessor):
    """
    Parses Ableton Live project files and extracts plugin information.
    """

    def __init__(
        self,
        project_path: str,
        scanner: Optional["PluginScanner"] = None,
        logger: Optional[DebugLogger] = None,
    ):
        """
        Initialize the analyzer with project path and optional scanner.
        """
        self.project_path = project_path
        self.project_plugins: List[ProjectPlugin] = []
        self.scanner = scanner
        self.logger = logger

    def analyze_project(self) -> List[ProjectPlugin]:
        """
        analyze the live project file and extract all plugin information
        """
        print(f"\nAnalyzing project: {os.path.normpath(self.project_path)}")

        # analysis phase start
        if self.logger:
            self.logger.log_debug("")
            self.logger.log_debug("===== PROJECT ANALYSIS PHASE =====")
            self.logger.log_debug(
                f"Parsing project file: {os.path.normpath(self.project_path)}"
            )

        try:
            root, is_gzipped, _ = self._parse_project_xml(self.project_path)

            # file format detection
            if self.logger:
                file_format = "gzipped (.als)" if is_gzipped else "plain XML"

            # find all VST2 and VST3 plugins in the project
            self._find_plugins(root, "vst2")
            self._find_plugins(root, "vst3")

            # analysis summary
            if self.logger:
                vst2_count = len(
                    [p for p in self.project_plugins if p.plugin_type == "vst2"]
                )
                vst3_count = len(
                    [p for p in self.project_plugins if p.plugin_type == "vst3"]
                )
                missing_count = len([p for p in self.project_plugins if p.is_missing])
                self.logger.log_debug(
                    f"Total plugins in project: {len(self.project_plugins)} (VST2: {vst2_count}, VST3: {vst3_count})"
                )
                self.logger.log_debug("")
                self.logger.log_debug(f"Missing plugins: {missing_count}")

                # log key plugins found
                if self.project_plugins:
                    for plugin in self.project_plugins[:5]:
                        status = "missing" if plugin.is_missing else "found"
                        self.logger.log_debug(
                            f"Found plugin: {plugin.name} (type: {plugin.plugin_type}, {status})"
                        )

            print(f"\nFound {len(self.project_plugins)} plugins in project")
            return self.project_plugins

        except Exception as e:
            print(f"Error analyzing project: {e}")
            if self.logger:
                self.logger.log_debug(f"Error analyzing project: {e}")
            return []

    def _find_plugins(self, root: ET.Element, plugin_type: str):
        """
        Find all plugins of specified type in the project XML.
        """
        info_tag = "VstPluginInfo" if plugin_type == "vst2" else "Vst3PluginInfo"

        for plugin_desc in root.iter("PluginDesc"):
            plugin_info = plugin_desc.find(info_tag)
            if plugin_info is not None:
                plugin = self._safe_execute(
                    lambda: self._parse_plugin_info(plugin_info, plugin_type),
                    f"Error parsing {plugin_type.upper()} plugin",
                )
                if plugin:
                    self.project_plugins.append(plugin)

    def _parse_plugin_info(
        self, plugin_info: ET.Element, plugin_type: str
    ) -> Optional[ProjectPlugin]:
        """
        Parse plugin information from XML element.
        """
        if plugin_type == "vst2":
            return self._parse_vst2_info(plugin_info)
        else:
            return self._parse_vst3_info(plugin_info)

    def _parse_vst2_info(self, vst_info: ET.Element) -> Optional[ProjectPlugin]:
        """
        Parse VST2 plugin information.
        """
        elems = {
            "path": vst_info.find("Path"),
            "name": vst_info.find("PlugName"),
            "uid": vst_info.find("UniqueId"),
            "buffer": vst_info.find(".//Buffer"),
        }

        if elems["name"] is None:
            return None

        plugin = ProjectPlugin(
            name=elems["name"].get("Value", ""),
            unique_id=elems["uid"].get("Value", "") if elems["uid"] is not None else "",
            path=elems["path"].get("Value", "") if elems["path"] is not None else "",
            plugin_type="vst2",
            parameter_data=elems["buffer"].text if elems["buffer"] is not None else "",
            xml_element=vst_info,
        )

        # check if plugin exists in database
        self._check_plugin_missing(plugin)
        return plugin

    def _check_plugin_missing(self, plugin: ProjectPlugin) -> None:
        """
        Check if plugin exists in installed plugins database.
        """
        if self.scanner and self.scanner.plugins:
            plugin_found = False

            # for VST2, try unique_id first
            if plugin.plugin_type == "vst2" and plugin.unique_id:
                plugin_found = any(
                    installed.unique_id == plugin.unique_id
                    and installed.plugin_type == "vst2"
                    for installed in self.scanner.plugins.values()
                )

            # fallback to name matching -- which seems more reliable
            if not plugin_found:
                plugin_found = any(
                    installed.name == plugin.name
                    and installed.plugin_type == plugin.plugin_type
                    for installed in self.scanner.plugins.values()
                )

            plugin.is_missing = not plugin_found
        else:
            if plugin.path:
                normalized_path = os.path.normpath(plugin.path)
                plugin.is_missing = not os.path.exists(normalized_path)
            else:
                plugin.is_missing = True

    def _parse_vst3_info(self, vst3_info: ET.Element) -> Optional[ProjectPlugin]:
        """
        Parse VST3 plugin information.
        """
        elems = {
            "name": vst3_info.find("Name"),
            "uid": vst3_info.find("Uid"),
            "unique_id": vst3_info.find("UniqueId"),
            "processor": vst3_info.find(".//ProcessorState"),
            "path": vst3_info.find("Path"),
        }

        if elems["name"] is None:
            return None

        # VST3 plugins use empty unique_id
        unique_id = ""

        # create plugin object
        plugin = ProjectPlugin(
            name=elems["name"].get("Value", ""),
            unique_id=unique_id,
            path=elems["path"].get("Value", "") if elems["path"] is not None else "",
            plugin_type="vst3",
            parameter_data=(
                elems["processor"].text if elems["processor"] is not None else ""
            ),
            xml_element=vst3_info,
        )

        self._check_plugin_missing(plugin)
        return plugin


class PluginMatcher:
    """
    Matches missing plugins with available VST3 replacements.
    """

    def __init__(
        self,
        installed_plugins: Dict[str, PluginInfo],
        project_plugins: List[ProjectPlugin],
        matching_config: Optional[Dict[str, Any]] = None,
        logger: Optional[DebugLogger] = None,
    ):
        """
        Initialize the matcher with plugins and configuration.
        """
        self.installed_plugins = installed_plugins
        self.project_plugins = project_plugins
        self.matches: List[Tuple[ProjectPlugin, List[PluginInfo]]] = []
        self.logger = logger

        config = matching_config or {}

        self.match_by_unique_id = bool(config.get("use_unique_id", True))
        self.match_by_name = True
        self.fuzzy_threshold = float(config.get("fuzzy_name_threshold", 0.8))
        self.prefer_newer_version = bool(config.get("prefer_newer_version", True))

    def find_matches(self) -> List[Tuple[ProjectPlugin, List[PluginInfo]]]:
        """
        Find VST3 replacements for all missing plugins.
        """
        print("\nLooking for missing plugins matches...")

        # matching phase start
        if self.logger:
            self.logger.log_debug("")
            self.logger.log_debug("===== MATCHING PHASE =====")
            strategy_parts = []
            if self.match_by_unique_id:
                strategy_parts.append("unique_id")
            if self.match_by_name:
                strategy_parts.append("name matching")
            self.logger.log_debug(
                f"Matching strategy: {', '.join(strategy_parts) if strategy_parts else 'none'}"
            )

        # only process VST2 plugins that are missing
        missing_vst2_plugins = [
            p for p in self.project_plugins if p.is_missing and p.plugin_type == "vst2"
        ]

        for project_plugin in missing_vst2_plugins:
            # processing plugin
            if self.logger:
                self.logger.log("")
                self.logger.log_debug(
                    f"Processing missing plugin: {project_plugin.name} (unique_id: {project_plugin.unique_id[:20] if project_plugin.unique_id else 'none'}...)"
                )

            if potential_matches := self._find_potential_matches(project_plugin):
                self.matches.append((project_plugin, potential_matches))
                # match found
                if self.logger:
                    self.logger.log_debug(
                        f"Found {len(potential_matches)} match candidate(s)"
                    )
            else:
                print(f"No matches were found for '{project_plugin.name}'")
                # no match
                if self.logger:
                    self.logger.log_debug(f"No matches found")

        # matching summary
        if self.logger:
            self.logger.log("")
            self.logger.log_debug(f"Total matches found: {len(self.matches)}")

        return self.matches

    def _find_potential_matches(
        self, project_plugin: ProjectPlugin
    ) -> List[PluginInfo]:
        """
        Find potential VST3 replacements.
        """
        if project_plugin.plugin_type != "vst2":
            return []

        matches = []

        for installed_plugin in self.installed_plugins.values():
            # only consider VST3 plugins as replacements
            if installed_plugin.plugin_type != "vst3":
                continue

            # match by unique ID first (is this really reliable?)
            if (
                self.match_by_unique_id
                and project_plugin.unique_id
                and installed_plugin.unique_id
                and project_plugin.unique_id == installed_plugin.unique_id
            ):
                matches.append(installed_plugin)
                # unique id match
                if self.logger:
                    self.logger.log_debug(
                        f"Match by unique_id: {installed_plugin.name}"
                    )
                continue

            # match by name (fallback: fuzzy matching)
            if self.match_by_name and self._names_match(
                project_plugin.name, installed_plugin.name
            ):
                matches.append(installed_plugin)
                # name match
                if self.logger:
                    match_type = self._get_match_type(
                        project_plugin.name, installed_plugin.name
                    )
                    self.logger.log_debug(
                        f"Match by name ({match_type}): {installed_plugin.name}"
                    )

        # apply preferences (sort by version, newer first)
        if self.prefer_newer_version:
            matches.sort(key=lambda p: self._version_key(p.version), reverse=True)

        return matches

    def _get_match_type(self, name1: str, name2: str) -> str:
        """
        Determine the type of name match for debugging.
        Returns match type string.
        """
        # normalize names
        n1 = re.sub(r"[^a-zA-Z0-9]", "", name1.lower())
        n2 = re.sub(r"[^a-zA-Z0-9]", "", name2.lower())

        if n1 == n2:
            return "exact"
        if n1 in n2 or n2 in n1:
            return "contains"

        # check cleaned names
        prefixes = ["the", "a", "an"]
        suffixes = ["vst", "vst2", "vst3", "x64", "x86", "64bit", "32bit"]

        n1_clean = n1
        n2_clean = n2
        for prefix in prefixes:
            n1_clean = (
                n1_clean[len(prefix) :] if n1_clean.startswith(prefix) else n1_clean
            )
            n2_clean = (
                n2_clean[len(prefix) :] if n2_clean.startswith(prefix) else n2_clean
            )
        for suffix in suffixes:
            n1_clean = (
                n1_clean[: -len(suffix)] if n1_clean.endswith(suffix) else n1_clean
            )
            n2_clean = (
                n2_clean[: -len(suffix)] if n2_clean.endswith(suffix) else n2_clean
            )

        if n1_clean == n2_clean and n1_clean:
            return "cleaned"

        # fuzzy match
        try:
            ratio = SequenceMatcher(None, n1_clean, n2_clean).ratio()
            if ratio >= self.fuzzy_threshold:
                return f"fuzzy ({ratio:.2f})"
        except Exception:
            pass

        return "unknown"

    def _names_match(self, name1: str, name2: str) -> bool:
        """
        Check if two plugin names match using fuzzy matching logic.
        """
        # normalize names: remove special characters, convert to lowercase
        n1 = re.sub(r"[^a-zA-Z0-9]", "", name1.lower())
        n2 = re.sub(r"[^a-zA-Z0-9]", "", name2.lower())

        # exact match
        if n1 == n2:
            return True

        # one contains the other (e.g., "Legend" in "The Legend")
        if n1 in n2 or n2 in n1:
            return True

        # clean names by removing common prefixes and suffixes
        prefixes = ["the", "a", "an"]
        suffixes = ["vst", "vst2", "vst3", "x64", "x86", "64bit", "32bit"]

        # remove prefixes and suffixes
        for prefix in prefixes:
            n1 = n1[len(prefix) :] if n1.startswith(prefix) else n1
            n2 = n2[len(prefix) :] if n2.startswith(prefix) else n2

        for suffix in suffixes:
            n1 = n1[: -len(suffix)] if n1.endswith(suffix) else n1
            n2 = n2[: -len(suffix)] if n2.endswith(suffix) else n2

        # check if cleaned names match
        if n1 == n2 and n1:
            return True

        # fuzzy matching -- handles typos and slight variations
        try:
            return SequenceMatcher(None, n1, n2).ratio() >= self.fuzzy_threshold
        except Exception:
            return False

    def _version_key(self, version: str) -> Tuple[int, Tuple[int, ...]]:
        """
        Convert version string to sortable tuple for version comparison.
        """
        if version.isdigit():
            return (1, (int(version),))
        parts = [int(p) for p in re.findall(r"\d+", version)] if version else []
        return (0, tuple(parts))


class PluginReplacer(XmlProcessor):
    """
    Handles the actual replacement of VST2 plugins with VST3 plugins in Ableton projects.
    """

    def __init__(
        self,
        project_path: str,
        dry_run: bool = False,
        logger: Optional[DebugLogger] = None,
    ):
        """
        Initialize the replacer with project path and dry-run mode.
        """
        self.project_path = project_path
        self.dry_run = dry_run
        self.logger: Optional[DebugLogger] = logger
        self.session_start = datetime.now()
        self.replaced_count = 0
        self.backup_path = ""

    def _create_element_with_value(
        self, parent: ET.Element, name: str, value: str = None
    ) -> ET.Element:
        """
        Create an XML element with optional value attribute.
        """
        elem = ET.SubElement(parent, name)
        if value is not None:
            elem.set("Value", value)
        return elem

    def _create_elements_from_data(
        self, parent: ET.Element, elements_data: List[Tuple[str, str]]
    ) -> None:
        """
        Create multiple XML elements from a list of (name, value) tuples.
        """
        # log batch creation summary
        if self.logger and elements_data:
            count = len(elements_data)
            self.logger.log_change("CREATE", f"Batch elements: {count} elements")

        for elem_name, value in elements_data:
            self._create_element_with_value(parent, elem_name, value)

    def _create_sub_elements(
        self, parent: ET.Element, sub_elements_data: List[Tuple[str, str]]
    ) -> None:
        """
        Create sub-elements with values from a list of (name, value) tuples.
        """
        # log sub-element creation summary
        if self.logger and sub_elements_data:
            count = len(sub_elements_data)
            parent_tag = parent.tag if parent is not None else "unknown"
            self.logger.log_change(
                "CREATE", f"Sub-elements in {parent_tag}: {count} elements"
            )

        for name, val in sub_elements_data:
            sub_elem = ET.SubElement(parent, name)
            sub_elem.set("Value", val)

    def start_logging(self, log_file: str = "apf.log", debug_level: int = 1):
        """
        Start logging session to a file.
        """
        # if logger already exists and is started, just log session details
        if self.logger and self.logger.log_fp:
            self.logger.log_debug("")
            self.logger.log_debug("===== REPLACEMENT PHASE =====")
            return

        # create logger if not provided
        if not self.logger:
            self.logger = DebugLogger(log_file, debug_level)

        # start logging session
        self.logger.start(self.session_start)

        # log session details
        self.logger.log(f"Project: {os.path.normpath(self.project_path)}")
        self.logger.log("")
        self.logger.log(f"Dry run: {self.dry_run}")
        self.logger.log("-" * 35)

    def stop_logging(self):
        """Stop logging and close the log file with session summary."""
        if self.logger:
            self.logger.stop()

    def create_backup(self):
        """
        Create a backup of the original project file.
        """
        shutil.copy2(self.project_path, self.backup_path)
        print(f"\nBackup created: {self.backup_path}")
        if self.logger:
            self.logger.log(f"Backup created: {self.backup_path}")

    def replace_plugin(
        self, project_plugin: ProjectPlugin, replacement: PluginInfo
    ) -> bool:
        """
        Replace a missing VST2 plugin with a VST3 replacement.
        """
        print(
            f"   Replacing '{project_plugin.name}' ({project_plugin.plugin_type}) with '{replacement.name}' ({replacement.plugin_type})"
        )

        if self.logger:
            self.logger.log("")
            self.logger.log(
                f"Replacing: {project_plugin.name} ({project_plugin.plugin_type}) → {replacement.name} ({replacement.plugin_type})"
            )
            self.logger.log(f"Replaced with: {replacement.path}")

            if (
                self.logger.debug_level >= DebugLogger.DEBUG_LEVEL_ADVANCED
                and replacement.version
            ):
                self.logger.log(f"Replacement version: {replacement.version}")

        if self.dry_run:
            print("   Dry-run enabled: skipping file modification.")

        try:
            # parse project file (handle both gzipped .als and plain XML)
            root, is_gzipped, original_content = self._parse_project_xml(
                self.project_path
            )

            # find the specific plugin element in the project XML
            plugin_element = self._find_plugin_element(root, project_plugin)
            if plugin_element is None:
                print(
                    f"\nError: Could not find plugin element for {project_plugin.name}"
                )
                if self.logger:
                    self.logger.log_error(
                        f"\nCould not locate plugin element for {project_plugin.name}"
                    )
                return False

            # perform the actual replacement/conversion
            if (
                project_plugin.plugin_type == "vst2"
                and replacement.plugin_type == "vst3"
            ):
                # convert VST2 to VST3 format
                self._convert_vst2_to_vst3(plugin_element, replacement, root)
            else:
                # simple path update for same-format replacements
                self._update_plugin_path(plugin_element, replacement)

            # save the modified project back to disk
            if not self.dry_run:
                self._save_project(root, is_gzipped, original_content)

            if self.logger:
                self.logger.log("")
                if self.dry_run:
                    self.logger.log("Replacement simulated (dry-run)")
                else:
                    self.logger.log("Replacement written")

            if self.logger:
                self.logger.log("")
            self.replaced_count += 1
            return True

        except Exception as e:
            print(f"\n  Error replacing plugin: {e}")
            if self.logger:
                self.logger.log_error(f"\n  Error during replace: {e}")
            return False

    def _find_plugin_element(
        self, root: ET.Element, project_plugin: ProjectPlugin
    ) -> Optional[ET.Element]:
        """
        Find the specific plugin element in the project XML that needs to be replaced.
        """
        for plugin_desc in root.iter("PluginDesc"):
            if project_plugin.plugin_type == "vst2":
                # look for VST2 plugin elements
                vst_info = plugin_desc.find("VstPluginInfo")
                if vst_info is not None:
                    name_elem = vst_info.find("PlugName")
                    if (
                        name_elem is not None
                        and name_elem.get("Value") == project_plugin.name
                    ):
                        return vst_info
            elif project_plugin.plugin_type == "vst3":
                # look for VST3 plugin elements
                vst3_info = plugin_desc.find("Vst3PluginInfo")
                if vst3_info is not None:
                    name_elem = vst3_info.find("Name")
                    if (
                        name_elem is not None
                        and name_elem.get("Value") == project_plugin.name
                    ):
                        return vst3_info
        return None

    def _indent_xml(self, elem: ET.Element, level: int = 0):
        """
        Add proper indentation to XML elements for better readability.
        """
        indent = "\n" + level * "\t"
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "\t"
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                self._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent

    def _convert_vst2_to_vst3(
        self, element: ET.Element, replacement: PluginInfo, root: ET.Element
    ):
        """
        Convert a VST2 plugin element to VST3 format.
        """
        # log vst2 element deletion
        if self.logger:
            plugin_name = element.find(".//PlugName")
            name_attr = (
                plugin_name.get("Value", "Unknown")
                if plugin_name is not None
                else "Unknown"
            )
            self.logger.log_change(
                "DELETE", f"Removed VST2 element: VstPluginInfo (PlugName: {name_attr})"
            )

        # create new VST3 plugin element
        vst3_info = ET.Element("Vst3PluginInfo")
        vst3_info.set("Id", "0")  # VST3 plugins use Id="0"

        # log vst3 element creation
        if self.logger:
            self.logger.log_change(
                "CREATE", f"Created VST3 element: Vst3PluginInfo (Id: 0)"
            )

        # add missing VST3 elements
        elements_data = [
            ("WinPosX", "47"),
            ("WinPosY", "72"),
            ("NumAudioInputs", "0"),
            ("NumAudioOutputs", "1"),
            ("IsPlaceholderDevice", "false"),
        ]
        self._create_elements_from_data(vst3_info, elements_data)

        # add preset structure
        preset_elem = ET.SubElement(vst3_info, "Preset")
        vst3_preset = ET.SubElement(preset_elem, "Vst3Preset")
        vst3_preset.set("Id", "1")  # VST3 presets use Id="1"

        # add missing Vst3Preset elements
        preset_elements = [
            ("OverwriteProtectionNumber", "3074"),
            ("MpeEnabled", "0"),
            ("MpeSettings", None),
            ("ParameterSettings", ""),
            ("IsOn", "true"),
            ("PowerMacroControlIndex", "-1"),
            ("PowerMacroMappingRange", None),
            ("IsFolded", "false"),
            ("StoredAllParameters", "true"),
            ("DeviceLomId", "0"),
            ("DeviceViewLomId", "0"),
            ("IsOnLomId", "0"),
            ("ParametersListWrapperLomId", "0"),
            ("Uid", None),
        ]

        # add preset elements
        for elem_name, value in preset_elements:
            if elem_name == "MpeSettings":
                mpe_settings = ET.SubElement(vst3_preset, "MpeSettings")
                self._create_sub_elements(
                    mpe_settings,
                    [
                        ("ZoneType", "0"),
                        ("FirstNoteChannel", "1"),
                        ("LastNoteChannel", "15"),
                    ],
                )
            elif elem_name == "PowerMacroMappingRange":
                power_range = ET.SubElement(vst3_preset, "PowerMacroMappingRange")
                self._create_sub_elements(power_range, [("Min", "64"), ("Max", "127")])
            elif elem_name == "Uid":
                uid_elem = ET.SubElement(vst3_preset, "Uid")
                self._create_element_with_value(vst3_preset, "DeviceType", "1")
            else:
                self._create_element_with_value(vst3_preset, elem_name, value)

        # copy parameter data from VST2 Buffer to VST3 ProcessorState
        buffer_elem = element.find(".//Buffer")
        if buffer_elem is not None and buffer_elem.text:
            proc_elem = ET.SubElement(vst3_preset, "ProcessorState")
            proc_elem.text = buffer_elem.text
            # log parameter data copy
            if self.logger:
                data_size = len(buffer_elem.text) if buffer_elem.text else 0
                self.logger.log_change(
                    "MODIFY",
                    f"Copied parameter data: VST2 Buffer → VST3 ProcessorState ({data_size} bytes)",
                )
        else:
            print(f"   Warning: No Buffer data found for {replacement.name}")

        # add remaining elements (ControllerState, Name, PresetRef)
        for elem_name in ["ControllerState", "Name", "PresetRef"]:
            value = "" if elem_name == "Name" else None
            self._create_element_with_value(vst3_preset, elem_name, value)

        # add plugin name element (outside preset)
        self._create_element_with_value(vst3_info, "Name", replacement.name)
        # add UID element outside preset
        self._add_vst3_uid(vst3_info, replacement)
        preset_uid_elem = vst3_preset.find("Uid")
        if preset_uid_elem is not None:
            # copy the UID fields from the main element to the preset
            main_uid_elem = vst3_info.find("Uid")
            if main_uid_elem is not None:
                for field in main_uid_elem.findall("Fields.*"):
                    new_field = ET.SubElement(preset_uid_elem, field.tag)
                    new_field.set("Value", field.get("Value", ""))

        # add DeviceType element
        self._create_element_with_value(vst3_info, "DeviceType", "1")

        # replace the old VST2 element with new VST3 element
        parent = self._find_parent_plugin_desc(root, element)
        if parent is not None:
            parent.remove(element)
            parent.append(vst3_info)

            # log element replacement
            if self.logger:
                self.logger.log_change(
                    "REPLACE",
                    f"Replaced VstPluginInfo with Vst3PluginInfo in PluginDesc",
                )

            # find the PluginDevice parent
            plugin_device = self._find_parent_plugin_device(root, parent)
            if plugin_device is not None:
                self._update_pointee_reference(plugin_device, replacement)
                self._update_branch_context(plugin_device, replacement)

    def _update_plugin_path(self, element: ET.Element, replacement: PluginInfo):
        """
        Update the plugin path for same-format replacements.
        """
        path_elem = element.find("Path")
        if path_elem is not None:
            old_path = path_elem.get("Value", "")
            path_elem.set("Value", replacement.path)
            # log path update
            if self.logger:
                self.logger.log_change(
                    "MODIFY", f"Updated plugin path: {replacement.path}"
                )

    def _find_parent_plugin_desc(
        self, root: ET.Element, element: ET.Element
    ) -> Optional[ET.Element]:
        """
        Find the parent PluginDesc element containing the given plugin element.
        """
        return next(
            (
                plugin_desc
                for plugin_desc in root.iter("PluginDesc")
                if element in plugin_desc
            ),
            None,
        )

    def _find_parent_plugin_device(
        self, root: ET.Element, plugin_desc: ET.Element
    ) -> Optional[ET.Element]:
        """
        Find the parent PluginDevice element containing the given PluginDesc element.
        """
        return next(
            (
                plugin_device
                for plugin_device in root.iter("PluginDevice")
                if plugin_desc in plugin_device
            ),
            None,
        )

    def _set_browser_path(self, browser_path_elem: ET.Element, replacement: PluginInfo):
        """
        Set the browser path to the modern format.
        """
        manufacturer = replacement.manufacturer or "Unknown"
        plugin_name = replacement.name

        # url encode manufacturer and plugin name for browser path
        encoded_manufacturer = urllib.parse.quote(manufacturer)
        encoded_plugin_name = urllib.parse.quote(plugin_name)

        # create modern browser path format
        new_path = f"view:X-Plugins#{encoded_manufacturer}:{encoded_plugin_name}"
        browser_path_elem.set("Value", new_path)

        # log browser path update
        if self.logger:
            self.logger.log_change("MODIFY", f"Updated BrowserContentPath: {new_path}")

    def _update_pointee_reference(
        self, plugin_device: ET.Element, replacement: PluginInfo
    ):
        """
        Update Pointee reference for Live 12+ compatibility.
        """
        pointee_elem = plugin_device.find(".//Pointee")
        if pointee_elem is not None:
            # remove Value attribute if it exists (Pointee should only have Id)
            if "Value" in pointee_elem.attrib:
                del pointee_elem.attrib["Value"]
                # log pointee update
                if self.logger:
                    self.logger.log_change(
                        "MODIFY", "Updated Pointee reference (removed Value attribute)"
                    )

    def _get_vst3_device_id(self, replacement: PluginInfo) -> str:
        """
        Generate proper VST3 device ID format from database.
        """
        # determine device type (instrument or effect)
        device_type = "instr"  # default to instrument
        if (
            "effect" in replacement.name.lower()
            or "compressor" in replacement.name.lower()
        ):
            device_type = "audiofx"

        # use unique_id from database as uuid
        if replacement.unique_id:
            return f"device:vst3:{device_type}:{replacement.unique_id}"
        else:
            msg = "Missing VST3 device identifier (unique_id) from database; cannot build BranchDeviceId."
            print(f"   Error: {msg}")
            raise ValueError(msg)

    def _update_branch_context(
        self, plugin_device: ET.Element, replacement: PluginInfo
    ):
        """
        Updates both BranchDeviceId and BranchSourceContext for proper
        plugin identification and browser navigation.
        """
        # update BranchDeviceId (unique device identifier)
        branch_device_id = plugin_device.find(".//BranchDeviceId")
        if branch_device_id is not None:
            # use proper vst3 device id format from database
            device_id = self._get_vst3_device_id(replacement)
            branch_device_id.set("Value", device_id)
            # log branch device id update
            if self.logger:
                self.logger.log_change("MODIFY", f"Updated BranchDeviceId: {device_id}")

        # update BranchSourceContext (browser navigation context)
        source_context = plugin_device.find("SourceContext")
        if source_context is not None:
            value_elem = source_context.find("Value")
            if value_elem is not None:
                branch_source_context = value_elem.find("BranchSourceContext")
                if branch_source_context is not None:
                    browser_path_elem = branch_source_context.find("BrowserContentPath")
                    if browser_path_elem is not None:
                        self._set_browser_path(browser_path_elem, replacement)

    def _uuid_to_uid_fields(self, device_id: str) -> List[int]:
        """
        Extract UUID from device ID and convert to VST3 UID fields (4 signed 32-bit integers).
        """

        if ":" in device_id:
            uuid_str = device_id.split(":")[-1]
        else:
            uuid_str = device_id

        # remove dashes from UUID string
        uuid_clean = uuid_str.replace("-", "")

        # split into four 32-bit chunks
        chunks = [uuid_clean[i : i + 8] for i in range(0, 32, 8)]

        # convert each chunk to a signed 32-bit integer
        fields = []
        for chunk in chunks:
            unsigned = int(chunk, 16)
            # convert to signed 32-bit integer
            if unsigned > 2**31 - 1:
                signed = unsigned - 2**32
            else:
                signed = unsigned
            fields.append(signed)

        return fields

    def _add_vst3_uid(self, preset_element: ET.Element, replacement: PluginInfo):
        """
        Add VST3 UID to preset element as XML structure.
        """
        uid_elem = preset_element.find("Uid")
        if uid_elem is None:
            uid_elem = ET.SubElement(preset_element, "Uid")

        # validate dev_identifier exists
        if not replacement.unique_id:
            print(f"\n  Warning: Could not find plugin UID for '{replacement.name}'")
            raise ValueError(
                "VST3 plugin missing dev_identifier in database; cannot generate UID"
            )

        # get UID fields from device identifier
        try:
            uid_values_int = self._uuid_to_uid_fields(replacement.unique_id)
        except ValueError as e:
            print(f"\n  Warning: Invalid plugin UID for '{replacement.name}'")
            raise ValueError(f"Invalid VST3 dev_identifier format: {e}")

        # create the 4 UID fields
        uid_fields = []
        for i in range(4):
            field = ET.SubElement(uid_elem, f"Fields.{i}")
            field.set("Value", str(uid_values_int[i]))
            uid_fields.append(str(uid_values_int[i]))

        # log uid
        if self.logger:
            self.logger.log_change(
                "CREATE",
                f"Created VST3 UID fields: [{', '.join(uid_fields[:2])}..] (4 fields)",
            )

    def _save_project(self, root: ET.Element, is_gzipped: bool, original_content: str):
        """
        Save the modified project back to disk.
        """
        # convert XML tree back to string
        self._indent_xml(root)
        xml_content = ET.tostring(root, encoding="utf-8", xml_declaration=False)

        # preserve original XML declaration if it existed
        if original_content and original_content.strip().startswith("<?xml"):
            first_line = original_content.split("\n")[0]
            final_content = first_line + "\n" + xml_content.decode("utf-8")
        else:
            final_content = xml_content.decode("utf-8")

        # ensure file ends with newline (like original)
        if not final_content.endswith("\n"):
            final_content += "\n"

        if is_gzipped:
            # write back as gzipped file
            with gzip.open(self.project_path, "wt", encoding="utf-8") as f:
                f.write(final_content)
        else:
            # write back as regular XML file
            with open(self.project_path, "w", encoding="utf-8") as f:
                f.write(final_content)


def load_config() -> Dict[str, Any]:
    """
    Load configuration from config.json file with fallback defaults.
    """
    # default configuration
    default = {
        "database": {"path": None},
        "matching": {
            "use_unique_id": True,
            "fuzzy_name_threshold": 0.9,
            "prefer_newer_version": False,
        },
        "safety": {
            "dry_run": True,
            "create_backup": True,
            "backup_suffix": ".bkp",
        },
        "output": {
            "debug_level": 1,
            "log_file": "apf.log",
        },
    }

    # try to load config.json; if missing, create one with defaults
    cfg_path = Path(__file__).with_name("config.json")
    try:
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            # merge loaded config with defaults
            for k, v in loaded.items():
                if (
                    isinstance(v, dict)
                    and k in default
                    and isinstance(default[k], dict)
                ):
                    default[k].update(v)
                else:
                    default[k] = v
        else:
            print("Config file missing.")
            print("Creating one..")
            try:
                with cfg_path.open("w", encoding="utf-8") as f:
                    json.dump(default, f, indent=2)
                print(f"config.json created at: {cfg_path}")
                print(
                    "Review and adjust the config.json file before running again. See README.md for details."
                )
                sys.exit(0)
            except Exception as write_err:
                print(f"Warning: failed to write default config.json: {write_err}")
                print("Proceeding with in-memory defaults.")
    except Exception as e:
        print(f"\nWarning: failed to load config.json: {e}")
    return default


def main():
    """
    Main function
    """
    print("\n" + "=" * 50)
    print("Ableton Plugin Fixer".center(50))
    print("=" * 50)

    # load configuration
    config = load_config()
    safety_config = config.get("safety", {})
    output_config = config.get("output", {})

    create_backup_flag = safety_config.get("create_backup", True)
    dry_run_flag = safety_config.get("dry_run", False)
    backup_suffix = safety_config.get("backup_suffix", ".bkp")
    log_file = output_config.get("log_file", "apf.log")
    debug_level = output_config.get("debug_level", 1)

    # get project file path from command line or input
    if len(sys.argv) > 1:
        # handle paths with spaces
        project_file = " ".join(sys.argv[1:])
    else:
        project_file = input("Enter path to Live project file: ").strip()
        if not project_file:
            print("You must provide a valid path\n")
            return

    # remove surrounding quotes if present
    if len(project_file) >= 2:
        if (project_file.startswith('"') and project_file.endswith('"')) or (
            project_file.startswith("'") and project_file.endswith("'")
        ):
            project_file = project_file[1:-1]

    # :shrugs:
    if os.name != "nt":
        project_file = project_file.replace("\\", "")

    # check if path exists
    if not os.path.exists(project_file):
        print(f"Project file not found: {os.path.normpath(project_file)}")
        print("Please provide a valid path\n")
        return

    # create logger
    logger = None
    session_start = datetime.now()
    if debug_level >= DebugLogger.DEBUG_LEVEL_NERDY:
        logger = DebugLogger(log_file, debug_level)
        logger.start(session_start)
        backup_path = f"{project_file}{backup_suffix}"
        logger.log(f"Project: {os.path.normpath(project_file)}")
        logger.log(f"Backup: {os.path.normpath(backup_path)}")
        logger.log("")
        logger.log(f"Dry run: {dry_run_flag}")

    # load plugins from Live database
    scanner = PluginScanner(config, logger=logger)
    installed_plugins = scanner.scan_plugins()

    # analyze project
    analyzer = ProjectAnalyzer(project_file, scanner, logger=logger)
    project_plugins = analyzer.analyze_project()

    # find matches
    matcher = PluginMatcher(
        installed_plugins,
        project_plugins,
        matching_config=config.get("matching", {}),
        logger=logger,
    )
    matches = matcher.find_matches()

    # check if there are any matches
    if not matches:
        print("No missing VST2 plugins found or no VST3 replacements available")
        return

    # show matches
    print("\nMatches:")
    for i, (project_plugin, potential_replacements) in enumerate(matches):
        print(
            f"\n{i+1}. {project_plugin.name} (ID: {project_plugin.unique_id}) -- Missing"
        )
        for j, replacement in enumerate(potential_replacements):
            print(f"   {j+1}. {replacement.name} ({replacement.plugin_type})")

    # replace mode
    # if there are matches, replace the plugins
    if matches:
        print("\n" + "=" * 50)
        print("Replace Mode".center(50))
        print("=" * 50)

        # create the replacer
        replacer = PluginReplacer(project_file, dry_run=dry_run_flag, logger=logger)
        replacer.backup_path = f"{project_file}{backup_suffix}"

        # create the backup
        if create_backup_flag and not dry_run_flag:
            replacer.create_backup()

        # start logging
        replacer.start_logging(log_file, debug_level)
        print(f"\nLogging to {log_file}")

        # notify if dry run is enabled
        if dry_run_flag:
            print(
                "\nDry Run mode enabled - changes will not be applied to project files. See README.md for details."
            )

        # replace the plugins
        for i, (project_plugin, potential_replacements) in enumerate(matches):
            print(f"\n{i+1}. Missing Plugin: {project_plugin.name}")

            if potential_replacements:
                for j, replacement in enumerate(potential_replacements):
                    print(f"   {j+1}. {replacement.name} ({replacement.plugin_type})")

                while True:
                    try:
                        choice = (
                            input(
                                f"\n   Choose (1-{len(potential_replacements)}) or 's' to skip: "
                            )
                            .strip()
                            .lower()
                        )
                        if choice == "s":
                            print(f"   Skipped {project_plugin.name}")

                            if replacer.logger:
                                replacer.logger.log("")
                                replacer.logger.log(
                                    f"Skipped: {project_plugin.name} ({project_plugin.plugin_type})"
                                )
                            break
                        elif choice.isdigit():
                            choice_idx = int(choice) - 1
                            if 0 <= choice_idx < len(potential_replacements):
                                selected_replacement = potential_replacements[
                                    choice_idx
                                ]

                                if replacer.replace_plugin(
                                    project_plugin, selected_replacement
                                ):
                                    print(f"\n   Successfully replaced!")
                                else:
                                    print(f"\n   Replacement failed!")
                                break
                            else:
                                print(
                                    f"Invalid choice. Please enter 1-{len(potential_replacements)} or 's'"
                                )
                        else:
                            print(f"Invalid input. Please enter a number or 's'")
                    except KeyboardInterrupt:
                        print(f"\nOperation cancelled")
                        break
            else:
                print(f"No automatic matches found; skipped {project_plugin.name}")

                if replacer.logger:
                    replacer.logger.log("")
                    replacer.logger.log(
                        f"Skipped: {project_plugin.name} ({project_plugin.plugin_type}) - No matches found"
                    )

        if not dry_run_flag and replacer.replaced_count > 0:

            try:
                _, is_gzipped, _ = replacer._parse_project_xml(project_file)
                save_format = "gzipped format" if is_gzipped else "plain XML format"
                if replacer.logger:
                    replacer.logger.log("")
                    replacer.logger.log_change(
                        "MODIFY", f"Saved project file ({save_format})"
                    )
                    replacer.logger.log("")
            except Exception:

                if replacer.logger:
                    replacer.logger.log("")
                    replacer.logger.log_change("MODIFY", "Saved project file")

    # summary
    print(f"\n\nSummary:")
    print(f"   Project plugins: {len(project_plugins)}")
    print(
        f"   Missing plugins: {len([p for p in project_plugins if p.is_missing and p.plugin_type == 'vst2'])}"
    )

    replaced_count = 0
    if not dry_run_flag and "replacer" in locals():
        replaced_count = getattr(replacer, "replaced_count", 0)

    print(f"   Replaced plugins: {replaced_count}")

    # stop logging
    if "replacer" in locals():
        replacer.stop_logging()


if __name__ == "__main__":
    main()
    input("Please enter something: ")
