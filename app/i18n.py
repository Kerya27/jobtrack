"""All user-facing strings live here, so another language can be added in one place.

Templates call ``t("key")``; Python code uses the same function.
"""

from typing import Final

from app.models import ApplicationStatus, EventType, WorkMode

DEFAULT_LANG: Final = "ru"

STRINGS: Final[dict[str, dict[str, str]]] = {
    "ru": {
        "app_name": "JobTrack",
        "nav_applications": "Отклики",
        "nav_stats": "Статистика",
        "nav_settings": "Настройки",
        "nav_logout": "Выйти",
        "nav_login": "Войти",
        "nav_register": "Регистрация",
        # auth
        "login_title": "Вход",
        "register_title": "Регистрация",
        "email": "Email",
        "password": "Пароль",
        "password_repeat": "Пароль ещё раз",
        "password_hint": "Минимум 8 символов",
        "login_button": "Войти",
        "register_button": "Создать аккаунт",
        "no_account": "Нет аккаунта?",
        "have_account": "Уже есть аккаунт?",
        "registration_closed": "Регистрация сейчас закрыта. Попроси владельца открыть её.",
        "err_invalid_email": "Похоже, это не email",
        "err_password_short": "Пароль должен быть не короче 8 символов",
        "err_password_long": "Пароль слишком длинный (максимум 72 байта)",
        "err_password_mismatch": "Пароли не совпадают",
        "err_email_taken": "Пользователь с таким email уже есть",
        "err_bad_credentials": "Неверный email или пароль",
        "err_csrf": "Сессия устарела. Обнови страницу и попробуй ещё раз.",
        "logged_out": "Ты вышел из аккаунта",
        "welcome": "Аккаунт создан. Вставь первую ссылку на вакансию!",
        # quick add
        "quick_add_title": "Новый отклик",
        "url_placeholder": "Вставь ссылку на вакансию",
        "parse_button": "Вытащить данные",
        "parsing": "Читаю вакансию…",
        "paste_text_button": "Вставить текст вакансии",
        "manual_button": "Заполнить вручную",
        "paste_text_title": "Текст вакансии",
        "paste_text_hint": "Скопируй сюда текст вакансии целиком: найду зарплату, остальное сохраню в описание.",
        "paste_text_submit": "Разобрать текст",
        "parse_ok": "Данные найдены — проверь и сохрани.",
        "parse_partial": "Нашёл немного. Проверь поля и дополни вручную.",
        "parse_failed": "Не удалось вытащить данные, заполни вручную или вставь текст вакансии.",
        "parse_blocked_site": "Сайт не отдал страницу (LinkedIn, Indeed и некоторые другие блокируют роботов). Заполни вручную или вставь текст вакансии.",
        "parse_bad_url": "Эту ссылку нельзя открыть: нужен публичный адрес http(s).",
        "parse_network": "Не получилось загрузить страницу (таймаут или сайт недоступен). Заполни вручную или вставь текст вакансии.",
        "parse_too_big": "Страница слишком большая для разбора. Заполни вручную или вставь текст вакансии.",
        "parse_not_html": "По ссылке не HTML-страница. Заполни вручную или вставь текст вакансии.",
        "parse_rate_limited": "Слишком много ссылок за час (лимит {limit}). Подожди немного или заполни вручную.",
        "text_parsed_salary": "Нашёл зарплату в тексте — проверь.",
        "text_parsed_no_salary": "Зарплату в тексте не нашёл. Текст сохранён в описание.",
        "only_save": "Пока только сохранить (ещё не откликнулся)",
        "save": "Сохранить",
        "cancel": "Отмена",
        "saved_ok": "Сохранено",
        "created_ok": "Отклик добавлен",
        # fields
        "f_company": "Компания",
        "f_position": "Позиция",
        "f_url": "Ссылка",
        "f_source": "Источник",
        "f_location": "Локация",
        "f_work_mode": "Формат работы",
        "f_salary_min": "Зарплата от",
        "f_salary_max": "Зарплата до",
        "f_salary_currency": "Валюта",
        "f_description": "Описание",
        "f_resume_version": "Версия резюме",
        "f_resume_version_hint": "Например, backend-v2 — чтобы сравнить, какое резюме работает",
        "f_contact_name": "Контакт",
        "f_contact_email": "Email контакта",
        "f_status": "Статус",
        "f_applied_at": "Дата отклика",
        "f_notes": "Заметки",
        "section_salary": "Зарплата",
        "section_contact": "Контакт",
        "more_fields": "Ещё поля",
        "err_required": "Обязательное поле",
        "err_too_long": "Слишком длинно (максимум {max} символов)",
        "err_url": "Нужна ссылка вида https://…",
        "err_int": "Нужно целое число",
        "err_negative": "Не может быть отрицательным",
        "err_salary_range": "«До» не может быть меньше «от»",
        "err_currency": "Код валюты из 3 букв, например CZK",
        "err_date": "Неверная дата",
        "err_invalid": "Неверное значение",
        # list
        "search_placeholder": "Поиск по компании или позиции",
        "show_archive": "Показать архив",
        "all": "Все",
        "empty_list": "Пока нет откликов — вставь первую ссылку выше.",
        "empty_filtered": "Ничего не найдено. Попробуй сбросить фильтры.",
        "last_activity": "Активность",
        "open": "Открыть",
        "applied_on": "отклик {date}",
        "archived_badge": "в архиве",
        "change_status": "Сменить статус",
        # card
        "back_to_list": "← Ко всем откликам",
        "timeline": "История",
        "timeline_empty": "Событий пока нет.",
        "add_event": "Добавить событие",
        "event_text": "Текст",
        "event_text_placeholder": "Например: позвонила HR, назначили интервью на четверг",
        "event_added": "Событие добавлено",
        "err_event_text": "Напиши текст события",
        "archive": "В архив",
        "unarchive": "Вернуть из архива",
        "archived_ok": "Отклик перенесён в архив",
        "unarchived_ok": "Отклик возвращён из архива",
        "delete": "Удалить",
        "delete_confirm": "Удалить отклик со всей историей? Это нельзя отменить.",
        "deleted_ok": "Отклик удалён",
        "status_changed": "Статус: {from_} → {to}",
        "status_initial": "Создан со статусом «{to}»",
        "not_found": "Не найдено",
        "not_found_text": "Такой страницы нет или у тебя нет к ней доступа.",
        # stats
        "stats_title": "Статистика",
        "stats_total": "Всего откликов",
        "stats_active": "Активных",
        "stats_response_rate": "Response rate",
        "stats_avg_days": "Дней до первого ответа",
        "stats_by_status": "По статусам",
        "stats_funnel": "Воронка",
        "stats_funnel_hint": "Считается по истории: отклик, который дошёл до интервью и потом получил отказ, всё равно учитывается как интервью. Ответом считается любая реакция компании, включая отказ.",
        "funnel_applied": "Откликнулся",
        "funnel_replied": "Получил ответ",
        "funnel_interview": "Интервью / тестовое",
        "funnel_offer": "Оффер",
        "stats_by_source": "По источникам",
        "stats_by_resume": "По версиям резюме",
        "stats_weekly": "Отклики по неделям (последние 12)",
        "col_group": "Группа",
        "col_applied": "Откликов",
        "col_replied": "Ответов",
        "col_rate": "Response rate",
        "col_interviews": "Интервью",
        "col_interview_rate": "% интервью",
        "not_set": "не указано",
        "stats_empty": "Статистика появится, когда будет хотя бы один отклик.",
        "week_of": "неделя с {date}",
        # settings
        "settings_title": "Настройки",
        "account": "Аккаунт",
        "change_password": "Сменить пароль",
        "current_password": "Текущий пароль",
        "new_password": "Новый пароль",
        "password_changed": "Пароль изменён",
        "err_wrong_password": "Неверный пароль",
        "data_title": "Данные",
        "export_csv": "Скачать CSV",
        "export_hint": "Все отклики в CSV (UTF-8). Тот же формат подходит для импорта.",
        "import_csv": "Импорт из CSV",
        "import_hint": "Колонки как в экспорте; обязательны company и position. Разделитель — запятая или точка с запятой.",
        "import_button": "Импортировать",
        "import_report": "Добавлено: {added}, пропущено: {skipped}",
        "import_no_file": "Выбери CSV-файл",
        "import_bad_file": "Не получилось прочитать файл: {error}",
        "danger_zone": "Удаление аккаунта",
        "delete_account_hint": "Удалит аккаунт и все отклики без возможности восстановления.",
        "delete_account": "Удалить аккаунт навсегда",
        "delete_account_confirm": "Точно удалить аккаунт и все данные?",
        "account_deleted": "Аккаунт и все данные удалены",
        "csv_row": "строка {row}: {reason}",
        "csv_too_big": "файл больше 5 МБ",
        "csv_not_utf8": "файл не в UTF-8 — сохрани его как «CSV UTF-8»",
        "csv_empty": "файл пустой",
        "csv_no_columns": "нужны колонки company и position",
        "csv_too_many_rows": "больше {max} строк",
        "employment_type": "Тип занятости: {value}",
        "csv_duplicate": "уже есть такой отклик",
        "csv_missing": "нет company или position",
        "csv_bad_value": "неверное значение в колонке {column}",
    }
}

STATUS_LABELS: Final[dict[str, dict[ApplicationStatus, str]]] = {
    "ru": {
        ApplicationStatus.SAVED: "Сохранён",
        ApplicationStatus.APPLIED: "Отклик отправлен",
        ApplicationStatus.REPLIED: "Ответили",
        ApplicationStatus.INTERVIEW: "Интервью",
        ApplicationStatus.TEST_TASK: "Тестовое",
        ApplicationStatus.OFFER: "Оффер",
        ApplicationStatus.REJECTED: "Отказ",
        ApplicationStatus.GHOSTED: "Молчат",
        ApplicationStatus.WITHDRAWN: "Отозван",
    }
}

WORK_MODE_LABELS: Final[dict[str, dict[WorkMode, str]]] = {
    "ru": {
        WorkMode.ONSITE: "Офис",
        WorkMode.HYBRID: "Гибрид",
        WorkMode.REMOTE: "Удалёнка",
        WorkMode.UNKNOWN: "Не указано",
    }
}

EVENT_TYPE_LABELS: Final[dict[str, dict[EventType, str]]] = {
    "ru": {
        EventType.STATUS_CHANGE: "Статус",
        EventType.NOTE: "Заметка",
        EventType.INTERVIEW: "Интервью",
        EventType.MESSAGE: "Сообщение",
    }
}


def t(key: str, lang: str = DEFAULT_LANG, **params: object) -> str:
    text = STRINGS[lang].get(key, key)
    return text.format(**params) if params else text


def status_label(status: ApplicationStatus | str, lang: str = DEFAULT_LANG) -> str:
    return STATUS_LABELS[lang][ApplicationStatus(status)]


def work_mode_label(mode: WorkMode | str, lang: str = DEFAULT_LANG) -> str:
    return WORK_MODE_LABELS[lang][WorkMode(mode)]


def event_type_label(event_type: EventType | str, lang: str = DEFAULT_LANG) -> str:
    return EVENT_TYPE_LABELS[lang][EventType(event_type)]
