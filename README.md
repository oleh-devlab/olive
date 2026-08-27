# O.L.I.V.E.
Operational Liaison for Intelligent Virtual Engagement

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white) [![Disnake](https://img.shields.io/badge/Disnake-Docs-5865F2?logo=discord&logoColor=white)](https://docs.disnake.dev/)

---

OLIVE is a modular hub designed to integrate various modules and services into a single, managed ecosystem, utilizing Discord as its primary control interface.

We didn't build the Discord bot from scratch; instead, we based it on the Flores project, which was previously used as a test and learning project. There is no history of the Flores bot's source code, but OLIVE is its successor.

Beyond its practical features, OLIVE serves as a hands-on learning environment. It is actively used to explore, practice, and reinforce software development concepts such as Object-Oriented Programming (OOP), database management, and modern architectural patterns. As such, while we strive for functionality, some features might be experimental or implemented primarily for educational purposes.

---

*Status:* We're fixing old features, writing new ones, and reworking the architecture.

---

## Submodules

This project relies on the following Git submodules to extend its core functionality:

- **[automatic_timetable_py](https://github.com/oleh-devlab/automatic_timetable_py)**: A scheduling engine written in Python. It utilizes the Google OR-Tools (CP-SAT solver) to solve scheduling problems. It calculates optimal schedules based on task deadlines, flexible/fixed routines, Pomodoro chunking, global breaks, and a Two-Stage priority architecture.
- **[inflation_calculator](https://github.com/oleh-devlab/inflation_calculator)**: An inflation calculator that computes the inflation-adjusted value of a past amount using monthly CPI data. Integrated into OLIVE to provide inflation commands and interactive UI through Discord. Records can be filed into budget groups, and the report renders either as the full tree of records under their groups or as group totals only (`/inflation view`).

---

## Expected file structure
```
.
├── .venv/                          # Virtual environment (ignored by Git)
├── docs/                           # Documentation files
│   ├── EN/
│   └── UK/
├── src/
│   ├── main.py                     # The main entry point of the bot
│   ├── settings.py                 # Local configuration (created by the user. Ignored by Git)
│   ├── tokens.json                 # Bot and module tokens (created by the user. Ignored by Git)
│   ├── phrases.json                # Local phrases configuration (ignored by Git)
│   ├── cogs/                       # Discord bot cogs (commands and events)
│   ├── core/                       # Core bot logic and utilities
│   ├── database/                   # Database logic and migrations
│   ├── modules/                    # Specialized submodules and extensions
│   ├── scripts/                    # Utility scripts (e.g., data migrations)
│   └── ...                         # The bot will store files like config.ini and sqlite3 databases here
├── tests/                          # Automated tests
├── README.md
├── .gitignore
├── .gitmodules                     # Git submodules configuration
├── pyproject.toml                  # Python project configuration
├── requirements.txt                # Tracked by Git
├── settings.py.example             # Tracked by Git
└── tokens.json.example             # Tracked by Git
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
    git submodule update --init --recursive
    ```
2. Create a Python virtual environment and activate it:
    ```bash
    python -m venv .venv

    # Windows
    .venv\Scripts\activate
    # Linux/MacOS
    source .venv/bin/activate
    ```
3. Copy and rename the example files into `src/`:
    ```bash
    cp settings.py.example src/settings.py
    cp tokens.json.example src/tokens.json
    ```
4. Fill in the required fields and settings in `settings.py` (see the comments inside the file).
5. Insert tokens for the Discord bot and other modules into the `tokens.json` file.
    - You can skip adding tokens for modules that are disabled (see `settings.py`).
6. Install dependencies.
    - You can skip installing dependencies for unused modules. Check `settings.py` to see which modules to disable and edit `requirements.txt` accordingly.
    ```bash
    pip install -r requirements.txt
    ```
    - If you have made changes to the `requirements.txt` file to exclude certain dependencies, we recommend reverting it to its original state after the installation is complete to avoid conflicts when working with Git.
    - *Tip:* You can quickly revert the file by running `git checkout -- requirements.txt` or `git restore requirements.txt`.
7. *(Optional)* Fill in `phrases.json`.
    - Comprehensive documentation for `phrases.json` is currently unavailable, so you will need to check the source code to fill in the parts you want to change. However, you can review the multi-server format documentation: [English](/docs/EN/walkthroughs/multi-server-phrases.md) | [Ukrainian](/docs/UK/walkthroughs/multi-server-phrases.md).
8. Run the bot:
    ```bash
    # Make sure you are in the `src` directory
    cd src
    python main.py
    ```

---

## Notes

- **LLM token budgets** are not a config file. They live in the bot's database, are seeded with defaults by the migrations, and are edited live with `/token_budget set`. (Older versions used an `llm_token_budget.json`; if you still have one in `src/`, it is no longer read and can be deleted.)
- **Reloading cogs.** `/reload_cogs` restarts a cog, but it does **not** pick up changes made in `src/core/` or `src/modules/` — those need a full bot restart. See [cog hot reloading](/docs/EN/cog_hot_reloading.md) | [Ukrainian](/docs/UK/cog_hot_reloading.md).