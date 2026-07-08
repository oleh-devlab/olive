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
3. Перемістити `settings.py.example` у каталог джерела (`src/`, або таким чином, щоби був на одному рівні з `main.py`).
4. Перейменувати `settings.py.example` в `settings.py` та заповнити необхідні поля (див. коментарі в файлі).
5. Файли з токенами створити у каталозі джерела (`src/`, в одній теці з `main.py` та `settings.py`) відповідно до їхніх імен у `settings.py`.
    - Можете не вставляти токени для модулів, які вимкнені (див. `settings.py`).
6. Переконатися, що використані шляхи до токенів (див. `settings.py`) є у `.gitignore`.
7. Встановити залежності.
    - Залежності з невикористовуваних модулів можна не встановлювати. Перегляньте `settings.py` для вимкнення непотрібних модулів і відредагуйте `requirements.txt` відповідно.
   ```bash
   pip install -r requirements.txt
   ```
   - Якщо ви редагували `requirements.txt` для пропуску залежностей, після встановлення рекомендується повернути його до початкового стану, щоб уникнути конфліктів при роботі git.
   - *Порада:* Ви можете швидко повернути файл до попереднього стану, виконавши команду `git checkout -- requirements.txt` або `git restore requirements.txt`.
8. *(опційно)* Заповнити `phrases.json`.
    - Основна документація про phrases.json поки що відсутня, тому треба дивитися код та заповнювати те, що ви хочете змінити. Але ви можете переглянути документацію мультисерверного формату: [Англійською](/docs/EN/walkthroughs/multi-server-phrases.md) | [Українською](/docs/UK/walkthroughs/multi-server-phrases.md).
9. Ініціалізувати базу даних SQLite. Це створить необхідні таблиці для роботи бота:
   ```bash
   cd src
   python scripts/init_database.py
   ```
10. Запустити бота:
   ```bash
   # Переконайтеся, що ви знаходитесь у каталозі `src`
   python main.py
   ```
