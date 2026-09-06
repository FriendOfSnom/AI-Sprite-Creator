# ST Sprite Creator

AI-powered character sprite generator for Student Transfer, using Google Gemini. Create complete characters with multiple outfits, expressions, and automatic background removal in a guided wizard.

---

## Features

- **Three creation modes**: Reference image, text prompt, or character fusion
- **Multiple outfits**: Casual, formal, athletic, swimsuit, uniform (custom too)
- **Expression generation**: Neutral, happy, sad, angry, surprised, and custom expressions
- **Automatic background removal** with alpha matting and manual touch-up tools
- **Configurable background color** for generation (default black)
- **Add to existing characters**: Add new outfits or expressions to previously created characters
- **Scale & height crop** with 77 reference sprites for accurate in-game sizing
- **Expression sheet generation** for visual reference
- **Sprite Tester** to preview characters in a simulated Ren'Py environment
- **Gemini Workshop** for quick img2img edits or text-to-image generation with style references
- **Game Sprite Importer**: crawl or import VN sprite rips and convert them into ST characters with deterministic (non-AI) pixel sorting
- **Sprite database** upload and download community-created characters

---

## Getting Started

### API Key Setup

This tool requires a Google Cloud API key with Gemini access.

1. Go to [Google AI Studio](https://aistudio.google.com)
2. Create a project and enable the Gemini API
3. Generate an API key
4. Enter it in the **API Settings** dialog on the launcher

New Google Cloud accounts get **$300 in free credits** - more than enough for hundreds of characters.

### Running the App

**Windows (recommended):**
Download the `.zip` from [Releases](https://github.com/FriendOfSnom/ST-Sprite-Creator/releases), extract, and double-click `ST Sprite Creator.exe`.

**Linux:**
Download the `.AppImage` from [Releases](https://github.com/FriendOfSnom/ST-Sprite-Creator/releases), then:
```bash
chmod +x ST-Sprite-Creator-v*.AppImage
./ST-Sprite-Creator-v*.AppImage
```
No Python or dependencies needed — everything is bundled in the AppImage.

**From source (any platform):**

```bash
pip install -r requirements.txt
python -m sprite_creator
```

---

## Launcher Modes

### Character Sprite Creator (Recommended)

The full character creation wizard. Walks you through every step:

1. **Source Selection** - Upload an image, describe a character, or fuse two together
2. **Character Setup** - Set name, voice, archetype; crop and normalize the character image
3. **Sprite Settings** - Set hair length, body type, and background color
4. **Generation Options** - Pick outfits and expressions to generate
5. **Review** - Confirm selections before generation begins
6. **Outfit Review** - Review generated outfits, adjust background removal, regenerate
7. **Expression Review** - Review expressions, touch up backgrounds
8. **Eye Line & Color** - Set eye position and pick name color
9. **Scale & Height** - Compare with reference sprites, adjust scale and height crop
10. **Complete** - View summary, upload to database, or open character folder

### Add to Character

Add new outfits or expressions to an existing character folder:

- Select a sprite from the character's existing images
- Normalize it to match AI output resolution
- Generate new outfits that match the existing character's style
- Add missing expressions or replace existing ones that came out poorly
- New outfits become new pose letters (c, d, e...)
- Existing settings (voice, name, eye line) are preserved

### Gemini Workshop

A standalone Gemini-powered image tool, separate from the main character pipeline:

- Load an image + prompt for img2img editing, or just type a prompt for text-to-image
- Optional Student Transfer style references for consistent art style
- Auto and manual background removal
- History gallery to keep results and click to reuse as input for iterative editing

### Game Sprite Importer

Import existing VN sprite rips and convert them into ST character folders — no AI involved anywhere in this flow:

- **Download** a gallery with the built-in crawler (E-Hentai/ExHentai, with resume support and ExHentai cookie login), or point at a local folder of images
- **Auto-sort** the images into pose → outfit → expression groups using pure pixel comparison. Sprite rips are composited variants of one base render, so images that differ only in the face are expressions of one outfit, and outfits sharing identical head pixels belong to one pose
- **Review grid**: assemble the detected pose stacks into characters with a few clicks; merge/split anything the sorter got wrong; discard CGs and junk
- **Finalize**: mid-thigh and chin crops (chin pre-suggested from where the expressions actually differ), eye line, name color, scale against reference sprites — then standard ST character folders are written with expression sheets and showChar files
- **Verification**: every outfit + expression combination from the rip is recomposited from the output folder and compared pixel-for-pixel against the original download; mismatches are flagged on the summary

Imports live in per-game workspaces (`~/.sprite_creator/imports/`) and are fully resumable — close the app mid-sort and pick up where you left off.

Also supported:
- **Multi-post games**: add more galleries/folders to an existing import ("Add More Images" on the first screen); new images are matched into existing poses automatically and new poses appear as extra cards — your sorting and characters are never touched
- **Pose-size normalization**: rip galleries ship poses at arbitrary canvas sizes; poses are automatically normalized against each other (by head height, with a per-pose nudge preview) so the single ST character scale works across all of them
- **Redo any character** after finalizing — crops, colors, and names are kept as starting values

### Expression Sheet Generator

Create expression reference sheet images from existing character folders. Useful for visual reference or sharing character designs.

### Sprite Database

Browse and download community-created characters from the shared sprite database. Upload your own characters directly from the finalize screen after creation.

### Sprite Tester

Preview character sprites in a simulated Ren'Py environment. Test outfit switching, expression changes, and the character loading system.

---

## Output Structure

```
character_name/
  character.yml             Metadata (voice, scale, eye line, colors)
  How-To-Use.txt            Instructions for using with Student Transfer
  original.png              Original character image for reference
  a/                        Pose A (first outfit)
    outfits/
      0.png                 Outfit image
    faces/face/
      0.png                 Neutral expression
      1.png                 Happy
      2.png                 Sad
      ...                   Additional expressions
  b/                        Pose B (second outfit)
    ...
  expression_sheets/        Generated reference sheets
```

Characters are compatible with Student Transfer's character system.

---

## Backup System

Full-size character images are automatically backed up before scaling. These backups are stored in `~/.sprite_creator/backups/` and provide the highest quality source images when adding new content to existing characters.

Use the **Clear Backups** button on the launcher to free disk space. Note that clearing backups means "Add to Character" will use the scaled (slightly lower quality) images from the character folder instead.

---

## Tips

- Every wizard step has a **?** help button and a tip bar with quick instructions
- Use the **ST Style** toggle to match Student Transfer art style, or disable it for any art style
- If Gemini's safety filters block a generation, the wizard shows a warning and continues
- You can regenerate individual outfits and expressions without starting over
- **View API Usage** on the launcher opens Google AI Studio's usage dashboard
- Change the **Generation BG** color if the default black clashes with your character's colors

---

## Troubleshooting

### API key not working

- Make sure you're using a Google Cloud API key (not a basic AI Studio free-tier key)
- Check your key in **API Settings** on the launcher
- Verify your account has credits at [AI Studio Usage](https://aistudio.google.com/usage)

### Outfit or expression generation blocked

- Gemini's safety filters may block certain content
- The wizard skips blocked items and continues with others
- Try regenerating with a different random seed
- Custom prompts are more likely to be blocked than random ones

### Background removal looks wrong

- Try a different **Generation BG** color that contrasts with your character
- Switch to **Manual** mode for click-based flood fill
- Use **Remove BG** on expression review for fine adjustments

### Next button is disabled

- **Character Setup**: Fill in Voice, Name, and Archetype, then accept your selection
- **Add to Character**: Normalize and accept the character sprite first
- **Review**: Check the acknowledgment box if a warning is shown
- **Expression Review**: Navigate through all outfits before continuing

### Log files

- Logs are saved to `logs/sprite_creator.log` next to the executable
- The log file resets on each launch
- Include log contents when reporting bugs

---

## Requirements

- **Python 3.10+** (when running from source)
- **Google Gemini API Key** (Google Cloud with credits)
- **Windows 10/11** or **Linux** (x86_64) — pre-built downloads available
- **macOS** — run from source

### Dependencies (installed via requirements.txt)

- google-generativeai
- Pillow (PIL)
- PyYAML
- rembg + onnxruntime (background removal)
- requests, beautifulsoup4

---

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

---

_AI-powered character sprite generation for Student Transfer_
