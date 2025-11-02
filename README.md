<h1 align="center">Ableton Plugin Fixer</h1>
<div align="center">
  <img src="https://www.glaurossi.com/assets/apf.jpg"  />
</div>

###

## What is Ableton Plugin Fixer?
**Ableton Plugin Fixer (APF)** is a tool designed for Ableton Live users, tackling the frustration of missing VST2 plugins by replacing them with their VST3 equivalents, preventing broken sessions and ensuring compatibility.

### Key features

 - **Preserves Everything**: All parameter settings; automation, presets and midi macros/mappings.
 - **Smart Matching**: Uses fuzzy name matching and unique plugin IDs.
 - **Safe Operation**: Creates backups before making changes.

> [!CAUTION]
> This is a work in progress tool that modifies Ableton Live project files. While the tool has built-in backup functionality, always work with your own backups and test thoroughly -- I'm not responsible for any data loss or project corruption.

## Installation

1. **Requirements**
   - Python 3.7+
   - macOS or Windows 10+
   - Ableton Live

2. **Download**
   - Git Clone

     ```bash 
     git clone https://github.com/glaurossi/Ableton-Plugin-Fixer
     cd Ableton-Plugin-Fixer
     ```
   - Or download the files [here](https://github.com/glaurossi/Ableton-Plugin-Fixer/releases)

3. **Usage**
   - Open your project in the latest version of Ableton Live installed on your system
   - Save a Copy
   - Run the saved copy through `apf.py`:

      ```bash
      py apf.py
      ```
      or
      ```bash
      python apf.py project.als
      ```

<h2 align="left">Showcase</h2>
<div align="center">
  <img src="https://www.glaurossi.com/assets/apf_showcase.avif"  />
</div>

## Configuration
The tool uses a `config.json` to control some of its behavior.

| Config                   | Description                                               | Default |
|--------------------------|-----------------------------------------------------------|---------|
| `database.path`          | Path to Live's plugin database                            | null    |
| `use_unique_id`          | Match plugins by their unique ID first.                   | true    |
| `fuzzy_name_threshold`   | (0.0-1.0): How similar plugin names need to be to match.  | 0.8     |
| `prefer_newer_version`   | Sort by newer first when multiple matches exist.          | false   |
| `dry_run`                | Preview changes without modifying files                   | true    |
| `create_backup`          | Automatically backup projects before changes              | true    |
| `backup_suffix`          | File extension for backups (e.g., ".bkp", ".backup").     | .bkp    |
| `debug_level`            | Log verbosity: 1 (minimal) to 3 (everything)              | 1       |
| `log_file`               | Where to save the logs.                                   | apf.log |

## Limitations
Some plugins may have different parameter layouts across versions, so presets might not transfer over.

## TODO

- [x]  Add macOS support
- [ ]  Support VST3 plugin updates (e.g., Kontakt 7 → Kontakt 8)
- [ ]  Fix .als extension in the root element
- [x]  Implement multi-level debug
    - Debug 1 (Minimal): Current behavior; essential logs only.
    - Debug 2 (Advanced): Debug 1 + logs of every change applied to project files.
    - Debug 3 (Nerdy): Debug 1 + Debug 2 + everything else.
- [ ]  Integrate Live's PluginScanner to refresh database before processing project files

## Contributions
Any improvements, bug fixes, or feature additions are welcome. Feel free to do so by [submitting a PR](https://github.com/glaurossi/Ableton-Plugin-Fixer/pulls).

## Issues

Found a bug or have a feature request? [Open an issue](https://github.com/glaurossi/Ableton-Plugin-Fixer/issues).

## License

MIT License - see [LICENSE](LICENSE) file for details.
