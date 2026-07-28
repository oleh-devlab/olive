# Setup Instructions
1. Склонувати репозиторій та перейти у його директорію:
   ```bash
   git clone https://github.com/oleh-devlab/olive.git
   cd olive
   git submodule update --init --recursive
   ```
2. Створити віртуальне середовище Python та активувати його:
    ```bash
    python -m venv .venv

    # Windows
    .venv\Scripts\activate
    # Linux/MacOS
    source .venv/bin/activate
    ```
3. Скопіювати та перейменувати файли-приклади у `src/`:
    ```bash
    cp settings.py.example src/settings.py
    cp tokens.json.example src/tokens.json
    cp llm_token_budget.json.example src/llm_token_budget.json
    ```
4. Заповнити необхідні поля та налаштування в `settings.py` (див. коментарі в файлі).
5. У файл `tokens.json` вставте токени для Discord бота та решти модулів.
    - Можете не вставляти токени для модулів, які вимкнені (див. `settings.py`).
6. Встановити залежності.
    - Залежності з невикористовуваних модулів можна не встановлювати. Перегляньте `settings.py` для вимкнення непотрібних модулів і відредагуйте `requirements.txt` відповідно.
   ```bash
   pip install -r requirements.txt
   ```
   - Якщо ви редагували `requirements.txt` для пропуску залежностей, після встановлення рекомендується повернути його до початкового стану, щоб уникнути конфліктів при роботі git.
   - *Порада:* Ви можете швидко повернути файл до попереднього стану, виконавши команду `git checkout -- requirements.txt` або `git restore requirements.txt`.
7. *(опційно)* Заповнити `phrases.json`.
    - Основна документація про `phrases.json` поки що відсутня, тому треба дивитися код та заповнювати те, що ви хочете змінити. Але ви можете переглянути документацію мультисерверного формату: [Англійською](/docs/EN/walkthroughs/multi-server-phrases.md) | [Українською](/docs/UK/walkthroughs/multi-server-phrases.md).
8. Запустити бота:
   ```bash
   # Переконайтеся, що ви знаходитесь у каталозі `src`
   cd src
   python main.py
   ```
