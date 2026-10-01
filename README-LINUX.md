# Перенос Telegram-планировщика на Debian/Ubuntu

Подготовлена локальная версия на основе `3fac75a87abd480792a8a96f20a24d31127aded8`.
Изменения не опубликованы в GitHub: обычный `git clone` этого коммита их не содержит.
На новый сервер нужно перенести именно подготовленные локальные файлы.
Все команды ниже предназначены для выполнения оператором; агент сервер не изменял.

## Поведение программы

Публикация — каждую пятницу в **19:10**, в `FRIDAY_TIMEZONE` (по умолчанию
`Asia/Yekaterinburg`). Часовой пояс ОС не определяет расписание или дату выбора фото.
Исходный алгоритм выбора «лосятницы»/«свинятницы» сохранён: точность pi 61,
`int(str(mp.pi)[14:][day])`, сумма дня, месяца, года и цифры pi, остаток по модулю 12,
«лосятница» при остатке 5.

`deerday.jpg` и `newfriday.jpg` читаются рядом с `TelegramMain.py`, независимо от cwd.
Перед запуском проверяются обе фотографии: обычный файл, непустой размер, открытие
и чтение. Это проверка доступности, а не проверка формата изображения Telegram.
Реальный `.env` при локальной подготовке не читался.

Процесс окружения имеет приоритет над `.env` рядом со скриптом. `.env` используется
только для отсутствующих переменных; пустая переменная процесса считается ошибкой,
а не поводом взять значение из `.env`. Подстановка `${...}` в `.env` отключена.
Unit запускает программу с `--no-dotenv`: единственный источник прикладных настроек
под systemd — `/etc/fridayscripts/telegram.env`.

Bot создаётся внутри async-контекста: инициализация включает запрос `getMe`, затем
запускается планировщик. После запуска администратору отправляется сообщение со
следующим временем публикации, включая UTC-смещение и IANA-зону. Если уведомление
не удалось, планировщик продолжает работать, а ошибка записывается в journald.
Статус `active` означает работающий процесс; получение сообщения о запуске проверяют отдельно.

Успех публикации сообщается только после ответа Telegram на `sendPhoto`.
Ошибка уведомления об успехе не повторяет фотографию. Ошибка фото передаётся
вызывающему коду, обрабатывается внутри задания и вызывает отдельное уведомление.
Сбой этого уведомления также фиксируется в журнале. В логах/уведомлениях ошибки
содержат тип исключения и контекст без исходного текста исключения, URL или traceback.
HTTP-логирование понижено до WARNING; итоговый formatter дополнительно скрывает токены.

**Автоматических повторов отправки нет.** Сетевой сбой или таймаут `sendPhoto`
означает неизвестный результат: Telegram мог принять фотографию. Сначала проверьте
канал; не повторяйте пост вслепую. Явные отказы API (`BadRequest`, `Forbidden`,
`RetryAfter`) отличаются от такого случая: после исправления причины/ожидания
оператор может отдельно решить повторить отправку. Повтор локальной проверки
`--check` безопасен. Повтор уведомления — отдельное действие, не повтор фото.
Доставка при недоступном Telegram не гарантируется.

Параметры задания: `max_instances=1` исключает параллельные экземпляры внутри одного
процесса; `coalesce=True` объединяет накопившиеся срабатывания в одно;
`misfire_grace_time=300` разрешает задержку до пяти минут в уже работающем процессе,
например после задержки event loop. Более позднее срабатывание пропускается и логируется.
Это не восстановление публикаций после выключения сервера: хранилище расписания
находится в памяти, при новом запуске выбирается следующее будущее срабатывание.
Если сервер был выключен в пятницу в 19:10, пропущенный пост автоматически не появляется.
Даже запуск в 19:11 не догоняет пост. Ручная публикация требует отдельного решения
после проверки канала. Межпроцессной/межсерверной блокировки нет.

SIGTERM/SIGINT прекращают запуск новых заданий; текущему заданию даётся до 90 секунд
на завершение фото и уведомления, затем оно отменяется без аварийного уведомления.
Общий таймаут фото — 45 секунд, уведомления — 15 секунд; unit ждёт остановку 120 секунд.
После отмены результат отправки тоже может быть неизвестен. Только после завершения
заданий закрываются scheduler и Bot. Ошибка конфигурации/запуска даёт код 1;
штатная остановка — код 0. `Restart=on-failure` не повторяет задания и не догоняет посты.
HTTP-клиенты также явно закрываются при ошибке инициализации Bot 22.0.

## Файлы и настройки

Минимальный комплект:

- `TelegramMain.py`, `deerday.jpg`, `newfriday.jpg`.
- `requirements-telegram.txt`, `constraints-telegram.txt`.
- `.env.example`, `deploy/fridayscripts.service`, эта инструкция.
- `tests/test_telegram.py` для локальных mock-проверок.

Настройки защищённого EnvironmentFile:

| Переменная | Требование |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Токен от BotFather, непустой, формат `число:секрет` |
| `TELEGRAM_CHANNEL_ID` | Отрицательный числовой ID канала или `@username` |
| `ADMIN_ID` | Положительный числовой ID личного чата администратора |
| `FRIDAY_TIMEZONE` | Доступная IANA-зона, например `Asia/Yekaterinburg` |

Администратор должен ранее открыть личный чат с ботом и отправить `/start`.
Бот должен иметь право публиковать сообщения в нужном канале.
Числовой channel ID предпочтителен: он не зависит от смены username.
Проверка формата переменных не подтверждает подлинность токена или права бота.

Не переносите Windows `venv`: создайте Linux venv заново. Прямые и транзитивные
Telegram-зависимости сохранены на версиях из исходного `req.txt`; массового обновления
нет. Пакеты для VK/aiogram/schedule не устанавливаются в Telegram venv.
`VKMain.py` и исходный `req.txt` сохранены; для VK используйте отдельный venv с
`pip install -r req.txt`. Не запускайте VK как постоянный сервис: скрипт создаёт
отложенные публикации при каждом запуске.

## Установка без запуска нового планировщика

Используйте системный Python 3.11 или новее. Перед установкой убедитесь, что
подготовленные файлы перенесены в `/tmp/fridayscripts-release` без секретов, `.git`
и venv. Например, на локальном Windows можно собрать архив:

```powershell
tar --exclude=__pycache__ --exclude=*.pyc -czf fridayscripts-linux.tar.gz TelegramMain.py deerday.jpg newfriday.jpg requirements-telegram.txt constraints-telegram.txt .env.example deploy tests README-LINUX.md VALIDATION.md
```

Передайте архив привычным способом. На новом сервере:

```bash
mkdir -p /tmp/fridayscripts-release
tar -xzf fridayscripts-linux.tar.gz -C /tmp/fridayscripts-release
sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates tzdata rsync
python3 --version
getent passwd fridayscripts || sudo useradd --system --user-group --home-dir /opt/fridayscripts --no-create-home --shell /usr/sbin/nologin fridayscripts
sudo install -d -o root -g fridayscripts -m 0750 /opt/fridayscripts
sudo rsync -r /tmp/fridayscripts-release/ /opt/fridayscripts/
sudo python3 -m venv /opt/fridayscripts/venv
sudo /opt/fridayscripts/venv/bin/python -m pip install -r /opt/fridayscripts/requirements-telegram.txt
sudo /opt/fridayscripts/venv/bin/python -m pip check
sudo chown -R root:fridayscripts /opt/fridayscripts
sudo chmod -R u+rwX,g+rX,o-rwx /opt/fridayscripts
sudo chmod 0750 /opt/fridayscripts
sudo chmod 0640 /opt/fridayscripts/TelegramMain.py /opt/fridayscripts/deerday.jpg /opt/fridayscripts/newfriday.jpg
sudo install -d -o root -g root -m 0700 /etc/fridayscripts
sudo install -o root -g root -m 0600 /opt/fridayscripts/.env.example /etc/fridayscripts/telegram.env
sudoedit /etc/fridayscripts/telegram.env
```

`sudoedit` — место ввода реальных настроек; не помещайте токен в shell-команды,
историю команд, скриншоты или журнал. EnvironmentFile использует строки `NAME=value`,
без `export` и shell-подстановок. Не публикуйте его содержимое. PID 1 читает файл
как root и передаёт настройки процессу, поэтому пользователю сервиса не требуется
прямой доступ к файлу. После редактирования проверьте только метаданные:

```bash
sudo stat -c '%U:%G %a %n' /etc/fridayscripts /etc/fridayscripts/telegram.env
sudo -u fridayscripts test -r /opt/fridayscripts/deerday.jpg
sudo -u fridayscripts test -s /opt/fridayscripts/deerday.jpg
sudo -u fridayscripts test -r /opt/fridayscripts/newfriday.jpg
sudo -u fridayscripts test -s /opt/fridayscripts/newfriday.jpg
cd /opt/fridayscripts
sudo -u fridayscripts /opt/fridayscripts/venv/bin/python -B -m unittest discover -s tests -v
sudo systemd-run --unit=fridayscripts-check --wait --pipe --collect \
  --property=User=fridayscripts --property=Group=fridayscripts \
  --property=WorkingDirectory=/opt/fridayscripts \
  --property=EnvironmentFile=/etc/fridayscripts/telegram.env \
  --property=ProtectSystem=strict --property=ProtectHome=true \
  --property=PrivateTmp=true --property=NoNewPrivileges=true \
  /opt/fridayscripts/venv/bin/python -B /opt/fridayscripts/TelegramMain.py --check --no-dotenv
sudo install -o root -g root -m 0644 /opt/fridayscripts/deploy/fridayscripts.service /etc/systemd/system/fridayscripts.service
sudo systemd-analyze verify /etc/systemd/system/fridayscripts.service
sudo systemctl daemon-reload
```

Ожидаемый offline check: код 0, обе фотографии доступны, следующее время — пятница
19:10 со смещением нужной зоны. Он не создаёт Bot, не вызывает Telegram API и не
отправляет сообщения. Отдельный API-режим не добавлен.

Готовый unit: [`deploy/fridayscripts.service`](deploy/fridayscripts.service).
Он использует абсолютный Python из venv, отдельного пользователя, journald,
`network-online.target`, `RestartSec=30` и максимум пять запусков за 300 секунд.
После исчерпания лимита исправьте причину и выполните `systemctl reset-failed`.
`network-online.target` не гарантирует доступность Telegram; проверьте используемый
службой сети wait-online механизм. `ProtectSystem=strict` оставляет файлы читаемыми,
`ProtectHome` совместим с расположением в `/opt`; DNS, TLS и исходящий HTTPS разрешены.

## Переключение со старого сервера

Выберите время вне публикации; предпочтительно задолго до пятницы 19:10.

1. Завершите установку и offline check на новом сервере, оставляя основной unit
   незапущенным. Зафиксируйте время последней публикации и настройки расписания старого.
2. На старом сервере остановите и отключите реальный сервис. Если он называется
   иначе, подставьте его имя: `sudo systemctl disable --now fridayscripts.service`.
   Проверьте `systemctl is-active`, отсутствие Python-процесса и отсутствие другого
   автозапуска через cron, supervisor, screen/tmux или второй unit.
3. Если остановка совпала с отправкой/таймаутом, проверьте канал и журналы до
   переключения. Если нельзя подтвердить остановку старого сервера, новый не запускайте.
4. Только после подтверждения остановки старого выполните на новом:

```bash
sudo systemctl enable --now fridayscripts.service
sudo systemctl is-enabled fridayscripts.service
sudo systemctl is-active fridayscripts.service
sudo journalctl -b -u fridayscripts.service --no-pager -n 100
```

Запуск рабочего сервиса обращается к Telegram и отправляет уведомление
администратору; далее публикация выполняется по расписанию. Локальные проверки
агента рабочего сервиса не запускали.

## Проверка restart и полной перезагрузки

Сначала после переключения выполните:

```bash
sudo systemctl restart fridayscripts.service
sudo systemctl is-enabled fridayscripts.service
sudo systemctl is-active fridayscripts.service
sudo systemctl show fridayscripts.service -p MainPID -p NRestarts -p ExecMainStatus
sudo journalctl -b -u fridayscripts.service --no-pager -n 100
pgrep -af '[p]ython.*TelegramMain.py'
timedatectl status
```

Проверьте: `enabled`, `active`, один процесс `TelegramMain.py`, PID соответствует
`MainPID`; в журнале после restart есть успешный запуск и следующее время; получено
новое сообщение о запуске. Сравните время в сообщении и журнале: пятница 19:10 в
`FRIDAY_TIMEZONE`, например для 1 октября 2026 — **2 октября 2026, 19:10 +05:00**.
Не должно быть циклических перезапусков или ошибок доступа к фото/конфигурации.
`ExecMainStatus=0` у работающего процесса сам по себе не подтверждает отправку.
Сверьте синхронизацию часов (NTP/chrony), а не только локальную зону ОС.
Отдельно подтвердите отсутствие старого процесса на старом сервере.

После согласованной полной перезагрузки нового сервера:

```bash
sudo reboot
# После повторного подключения:
sudo systemctl is-enabled fridayscripts.service
sudo systemctl is-active fridayscripts.service
sudo systemctl show fridayscripts.service -p MainPID -p NRestarts -p ExecMainStatus
sudo journalctl -b -u fridayscripts.service --no-pager
pgrep -af '[p]ython.*TelegramMain.py'
timedatectl status
```

Ожидаются один процесс, новое уведомление о запуске и корректное следующее время.
`journalctl -b` показывает текущую загрузку. Отсутствие уведомления при `active`
требует проверки ошибки уведомления в журнале, доступа администратора и сети.
Фактическую публикацию и право бота отправлять фото подтверждают наблюдением
плановой публикации либо отдельной явно разрешённой ручной проверкой; offline check
и сообщение о запуске этого не подтверждают.

## Откат

1. На новом: `sudo systemctl disable --now fridayscripts.service`.
2. Подтвердите остановку процесса через `systemctl is-active` и `pgrep`, дождитесь
   завершения отправки. При неизвестном результате обязательно проверьте канал.
3. Только после этого на старом включите ранее работавший способ запуска
   (для systemd: `sudo systemctl enable --now fridayscripts.service`).
4. Проверьте процесс, журнал, расписание и наличие единственного активного
   планировщика на двух серверах. Пропущенные публикации автоматически не восполняйте.

## Подтверждено кодом

Проверка обязательных переменных без вывода значений; приоритет окружения;
явная зона для cron и даты выбора фото; абсолютные пути; прежний алгоритм;
разделение публикации и уведомлений; отсутствие повторов фото; обработка ошибок
в задании; параметры coalesce/misfire/max_instances; ожидание текущей отправки;
Bot async context; offline режим; ненулевой код фатальной ошибки; защита логов от токена.
Результаты реально выполненных локальных проверок записаны в `VALIDATION.md`.

API сверены с [Bot v22.0](https://docs.python-telegram-bot.org/en/v22.0/telegram.bot.html),
[HTTPXRequest v22.0](https://docs.python-telegram-bot.org/en/v22.0/telegram.request.httpxrequest.html),
[ошибками v22.0](https://docs.python-telegram-bot.org/en/v22.0/telegram.error.html),
[руководством APScheduler 3.11.0](https://github.com/agronholm/apscheduler/blob/3.11.0/docs/userguide.rst)
и [исходником asyncio executor 3.11.0](https://github.com/agronholm/apscheduler/blob/3.11.0/src/apscheduler/executors/asyncio.py).
Приоритет `.env` проверен по [python-dotenv 1.1.0](https://github.com/theskumar/python-dotenv/blob/v1.1.0/src/dotenv/main.py).

## Нужно проверить на сервере

Доступность Telegram, DNS и TLS; реальные права бота в канале; возможность писать
администратору; фактические переменные EnvironmentFile; чтение обеих фотографий
под пользователем и ограничениями unit; установка зависимостей на Linux;
`systemd-analyze verify`; часы и синхронизация времени; wait-online;
restart/reboot и SIGTERM/SIGINT на Linux; отсутствие второй копии и состояние старого
сервиса; фактический ответ Telegram на фото. Эти проверки не заменяются mock-тестами.
