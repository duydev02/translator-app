# Building the Windows executable

## Prerequisites

- Python 3.10 or later (standard CPython; Tkinter ships with it).
- PyInstaller.
- *(Optional)* `tkinterdnd2` for drag-and-drop file loading.

```powershell
pip install pyinstaller
pip install tkinterdnd2       # optional but recommended
```

## One-click build

From the project root:

### PowerShell
```powershell
.\scripts\build.ps1
```

### cmd.exe
```cmd
scripts\build.bat
```

Both wrappers just invoke PyInstaller with `Translator.spec`.

## Manual build

```powershell
pyinstaller Translator.spec
```

Output:

```
dist/
└── Translator.exe
```

The icon, version metadata, and drag-and-drop hooks are wired up by the spec file.
If you edit the spec, that's the only file you need to commit — everything else
derives from it.

## Distributing

Ship the exe alongside your schema JSON:

```
TranslatorApp/
├── Translator.exe              (from dist/)
└── db_schema_output.json       (the data this team uses)
```

Optional extras users can drop in:
- `translator_custom_map.json` — shared team overrides
- `translator_exclusions.txt` — shared exclusion rules

All of those will be picked up from the exe's folder at runtime.

## Clean rebuild

```powershell
Remove-Item -Recurse -Force build, dist
pyinstaller Translator.spec
```

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| `image.ico not found` during build | Run PyInstaller from the project root — the spec uses relative paths (`assets/image.ico`, `assets/version.txt`). |
| `db_schema_output.json` error dialog on first run | The exe expects this file **next to itself**. Copy one in. |
| Drag-and-drop does nothing | `tkinterdnd2` wasn't installed when the exe was built. Reinstall and rebuild. |
| Icon looks pixelated in Explorer's Large-icon view | Re-generate `assets/image.ico` with multiple resolutions inside (16, 32, 48, 64, 128, 256 px). |
