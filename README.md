# O.L.I.V.E.
Operational Liaison for Intelligent Virtual Engagement

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white) [![Disnake](https://img.shields.io/badge/Disnake-Docs-5865F2?logo=discord&logoColor=white)](https://docs.disnake.dev/)

---

OLIVE is a modular hub designed to integrate disparate microservices into a single, managed ecosystem, utilizing Discord as its primary control interface.

We didn't build the Discord bot from scratch; instead, we based it on the Flores project, which was previously used as a test and learning project. The source code for the Flores bot is not publicly available (it has been lost), but OLIVE is its successor.

---

*Status:* We are currently refactoring the old version of the code. Product descriptions may not reflect reality.

---

## Expected file structure
```
.
├── .venv/              # Virtual environment (ignored by Git)
├── docs/               # Documentation files (setup instructions, walkthroughs)
│   ├── EN/
│   └── UK/
├── src/
│   ├── main.py         # The main entry point of the bot
│   ├── settings.py     # Local configuration (created by the user based on settings.py.example. Ignored by Git)
│   ├── phrases.json    # Local phrases configuration (ignored by Git)
│   ├── cogs/
│   ├── core/
│   ├── modules/
│   ├── ...             # The bot will store files such as llm_context.json, config.ini, currency_cache.json, and others in this (src/) folder.
│   └── .env            # And the rest of the tokens with the names you specify in settings.py (should be in .gitignore)
├── README.md
├── .gitignore
├── requirements.txt    # Tracked by Git
└── settings.py.example # Tracked by Git
```

---

## Setup Instructions

### Ukrainian
[Ukrainian version of the initial setup instructions](/docs/UK/setup-instructions.md) located in `docs/UK/setup-instructions.md`.

### English

1. Clone the repository and navigate to its directory:
```bash
git clone https://github.com/oleh-devlab/olive.git
cd olive
```
2. Create a Python virtual environment and activate it:
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/MacOS
source .venv/bin/activate
```
3. Move `settings.py.example` to the source folder (into `src/`, or such that it is on the same level as `main.py`).
4. Rename `settings.py.example` to `settings.py` and fill in the required fields (see the comments inside the file).
5. Create token files in the source folder (`src/`, in the same folder as `main.py` and `settings.py`) according to their names defined in `settings.py`.
- You can skip adding tokens for modules that are disabled (see `settings.py`).
6. Ensure that the token paths you use (see `settings.py`) are listed in `.gitignore`.
7. Install dependencies.
- You can skip installing dependencies for unused modules. Check `settings.py` to see which modules to disable and edit `requirements.txt` accordingly.
```bash
pip install -r requirements.txt
```
- If you have made changes to the `requirements.txt` file to exclude certain dependencies, we recommend reverting it to its original state after the installation is complete to avoid conflicts when working with Git.
- *Tip:* You can quickly revert the file by running `git checkout -- requirements.txt` or `git restore requirements.txt`.
8. *(Optional)* Fill in `phrases.json`.
    - Comprehensive documentation for `phrases.json` is currently unavailable, so you will need to check the source code to fill in the parts you want to change. However, you can review the multi-server format documentation: [English](/docs/EN/walkthroughs/multi-server-phrases.md) | [Ukrainian](/docs/UK/walkthroughs/multi-server-phrases.md).

9. Run the bot (it is recommended to run this from the `src` directory, or the one containing `main.py`):
```bash
cd src
python main.py
```