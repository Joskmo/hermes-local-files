# Установка через ИИ-агента

Этот документ предназначен для Hermes Agent, Codex, Claude Code и других
агентов с доступом к терминалу. Пользователю не нужно пересказывать архитектуру:
агент должен прочитать этот файл, выполнить проверки и показать доказательства.

## Результат, который считается успешным

Установка завершена только когда подтверждены все пункты:

1. server doctor возвращает `ok: true`;
2. macOS doctor возвращает `ok: true`;
3. Syncthing не слушает публичные интерфейсы;
4. restricted SSH key принимает только разрешённый loopback forward;
5. тестовый файл проходит Mac → server;
6. другой тестовый файл проходит server → Mac;
7. удаления проходят в обе стороны;
8. Hermes project указывает на точный проверенный путь под `/srv`;
9. после краткого разрыва tunnel синхронизация восстанавливается сама.

Unit-тесты не заменяют эти проверки. Если Mac недоступен, агент должен написать
`server installed; macOS and E2E not verified`, а не `готово`.

## Протокол CLI

Канонический интерфейс находится в `./bin/hermes-local-files`.

```bash
./bin/hermes-local-files version
./bin/hermes-local-files doctor --scope auto --json
./bin/hermes-local-files install-server --data-root "/srv/hermes-local-files/<profile>"
./bin/hermes-local-files install-macos \
  --ssh-target "<user>@<server>" \
  --host-key-sha256 "SHA256:<verified-ed25519-fingerprint>" \
  --connection-id "<hermes-desktop-connection-id>" \
  --profile "<profile>"
./bin/hermes-local-files status --scope auto --json
```

`doctor --json` всегда печатает один JSON-объект:

```json
{
  "schema_version": 1,
  "ok": false,
  "scope": "macos",
  "checks": [
    {
      "id": "ssh-tunnel",
      "ok": false,
      "detail": "not listening",
      "remedy": "Check SSH reachability and the tunnel LaunchAgent log."
    }
  ]
}
```

Правила для агента:

- проверять exit code и поле `ok`;
- не извлекать секреты из config для отчёта;
- исправлять каждый failed check по `id`, затем запускать полный doctor заново;
- не удалять пользовательские файлы и mappings во время repair;
- не использовать `--yolo`, `StrictHostKeyChecking=no` или `accept-new`;
- не менять Hermes core;
- не объявлять E2E успешным по одному `connected=true`.

## Шаг 1. Проверить исходники

В корне checkout выполнить:

```bash
npm run check
npm test
hermes plugins doctor . --ci
```

Остановиться, если хотя бы одна команда завершилась ненулевым кодом.

Зафиксировать:

```bash
git rev-parse HEAD
git status --short
```

Для production-установки checkout должен быть чистым, а commit должен совпадать с
тем, который пользователь решил установить.

## Шаг 2. Установить серверную часть

Сначала получить имя целевого профиля и server data root от пользователя. Не
подставлять значения из истории других установок. Затем установить plugin:

```bash
export HERMES_PROFILE="<profile>"
export HERMES_HOME="$HOME/.hermes/profiles/$HERMES_PROFILE"
hermes plugins install Joskmo/hermes-local-files --enable
```

Если репозиторий private и сервер не авторизован на GitHub, не просить personal
access token в чате. Использовать уже настроенный credential helper либо передать
проверенный checkout по существующему административному SSH-каналу.

Из каталога установленного plugin выполнить:

```bash
./bin/hermes-local-files install-server \
  --data-root "/srv/hermes-local-files/$HERMES_PROFILE"
./bin/hermes-local-files doctor --scope server --json
```

Installer не требует root: Syncthing работает как systemd user service, а данные
растут на `/srv`. Не переносить database/version history обратно на маленький
root-раздел.

После enable завершить только process выбранного профиля, если он уже работает.
Hermes Desktop переподнимет его. Не перезапускать посторонние messaging gateway.

### Проверка сервера

JSON doctor должен подтвердить:

- `private-config`;
- `projects-root`;
- `syncthing-service`;
- `syncthing-network`;
- `syncthing-api`;
- `backend-plugin`.

Дополнительно проверить runtime listeners. Допустимы только loopback addresses для
Syncthing GUI/API и sync protocol.

## Шаг 3. Установить Mac

Работать в локальном checkout того же commit:

```bash
./bin/hermes-local-files install-macos \
  --ssh-target "<user>@<server>" \
  --ssh-port 22 \
  --host-key-sha256 "SHA256:<verified-ed25519-fingerprint>" \
  --connection-id "<hermes-desktop-connection-id>" \
  --profile "<profile>"
./bin/hermes-local-files doctor --scope macos --json
```

Первый запуск может запросить существующую административную SSH-аутентификацию,
чтобы добавить отдельный restricted key. Агент может открыть интерактивный PTY,
но не должен просить пользователя прислать пароль или private key текстом.

Fingerprint должен быть получен по независимому доверенному каналу. Installer
обязан остановиться, если ED25519 fingerprint сервера отличается от переданного.
Запрещено обходить эту проверку.

### Проверка Mac

JSON doctor должен подтвердить:

- owner-only config;
- приватную конфигурацию Syncthing;
- три загруженных LaunchAgent;
- companion health;
- SSH tunnel;
- Desktop plugin без незаменённых placeholders;
- доступный project inventory.

После установки один раз перезапустить Hermes Desktop или выполнить штатную
команду reload desktop plugins.

## Шаг 4. Создать тестовый проект

1. Создать новую пустую папку с уникальным именем в пользовательском каталоге.
2. В Hermes открыть `Local Files`.
3. Нажать `Добавить папку` и выбрать её.
4. Дождаться `Синхронизировано`.
5. Получить mapping через CLI/companion inventory без печати token/API key.
6. Проверить через `projects.list`, что `primary_path` равен `server_path` mapping.

Если provisioning оборвался, не создавать новую папку с тем же именем вслепую.
Сначала проверить существующий mapping и server manifest, затем продолжить
идемпотентную операцию.

## Шаг 5. E2E в обе стороны

Использовать только уникальные текстовые marker-файлы без пользовательских данных.

### Mac → server

1. Записать marker в локальную тестовую папку.
2. Дождаться healthy status.
3. Прочитать тот же относительный путь на сервере.
4. Сравнить точное содержимое.

### Server → Mac

1. Записать другой marker в server project root.
2. Дождаться healthy status.
3. Прочитать его в локальной папке.
4. Сравнить точное содержимое.

### Удаления и reconnect

1. Удалить первый marker на Mac и дождаться удаления на server.
2. Удалить второй marker на server и дождаться удаления на Mac.
3. Остановить только tunnel LaunchAgent.
4. Создать третий marker локально.
5. Запустить tunnel и подтвердить автоматическое convergence.
6. Удалить тестовую папку/project только после отдельного согласия пользователя.

## Как сообщать результат

Разделять четыре статуса:

| Статус | Что доказано |
|---|---|
| Source verified | tests, static checks и Plugin Doctor проходят |
| Server ready | server doctor и loopback listeners проверены |
| Mac ready | macOS doctor и LaunchAgent проверены |
| E2E ready | create/delete/reconnect проверены в обе стороны |

При ошибке указать failed check ID, фактическое наблюдение и следующий безопасный
шаг. Не включать в ответ config contents, API keys, local tokens, OAuth данные и
private keys.
