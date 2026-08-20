<div align="center">

# ClaudeGuard

**VPN-whitelist защита для Claude на Linux.**

Блокирует приложение **Claude Desktop** (Electron), утилиту **Claude Code CLI** (`claude`) и все домены Claude/Anthropic (`claude.ai`, `claude.com`, `anthropic.com` и все их поддомены), если внешний IP устройства не входит в ваш белый список VPN - чтобы вы случайно не зашли в Claude не из того региона.

[English](README.md) · **Русский**

</div>

---

## Зачем

Если аккаунт чувствителен к региону, один запрос с не того IP может привести к флагу аккаунта.
ClaudeGuard решает эту проблему: каждый запуск и каждая активная сессия сверяются с белым списком ваших разрешенных IP, и доступ отрубается в момент, когда вы с него слетаете.

## Возможности

- **Белый список IP** - Claude открывается **только** при совпадении внешнего IP с вашими VPN-нодами.
- **Мгновенный pre-flight** - при запуске `claude` внешний IP проверяется за < 0.5с (STUN по UDP). Нет в списке = блок ещё до подключения.
- **Единый мозг, один вердикт** - фоновый демон публикует решение в state-файл; CLI и лаунчер читают один и тот же вердикт, поэтому компоненты не расходятся.
- **Автозапуск при входе** через systemd user service.
- **Два режима демона** - headless (systemd, для серверов) или system tray (для десктопов с X11/Wayland через pystray).
- **Заморозка автообновлений** - блокирует серверы обновлений и локает директории обновлений.
- **Модель по умолчанию** - закрепите модель для каждой сессии `claude` (например `claude-opus-4-8`); обёртка CLI автоматически подставляет `--model`.
- **Защита Claude Desktop** - находит и убивает Electron-based Claude Desktop, если IP не в whitelist.

## Установка

Установщик компилирует C root-хелпер **прямо на вашей машине** через gcc/clang.
Python 3 и iptables ставятся автоматически при необходимости (pacman/apt/dnf/zypper/nix-env).

Два способа установки - результат абсолютно одинаковый.

**Вариант 1 - одной строкой** (скачает и соберёт с GitHub):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/anytcp/ClaudeGuard/main/install.sh)"
```

**Вариант 2 - из локальной копии** (склонированное или скачанное репо):

```bash
./install.sh
```

Установщик:
1. Скомпилирует C root-хелпер через gcc.
2. Спросит, какой режим нужен: **system tray** (требует pystray + Pillow) или **headless** (чистый systemd). Если display server не обнаружен, автоматически ставится headless.
3. Создаст systemd user service для демона и systemd path unit для root-хелпера.
4. Повесит перехватчик на команду `claude`.
5. Один раз спросит пароль (`sudo` нужен для `/etc/hosts` и iptables).
6. Предложит добавить текущий IP в whitelist - соглашайтесь, **только если вы сейчас под VPN.**

**Поддерживаемые дистрибутивы:** Arch, Debian/Ubuntu, Fedora, openSUSE, NixOS - всё, где есть systemd и компилятор C.

**Удаление:**

```bash
./uninstall.sh
```

## CLI

После установки доступна команда `claudeguard`:

| Команда | Что делает |
|---|---|
| `claudeguard status` | Текущий статус, IP и whitelist |
| `claudeguard add-ip <ip>` | Добавить IP или CIDR в whitelist |
| `claudeguard remove-ip <ip>` | Удалить IP из whitelist |
| `claudeguard list-ips` | Показать whitelist |
| `claudeguard enable-protection` / `disable-protection` | Включить / выключить защиту |
| `claudeguard block-updates` / `allow-updates` | Заблокировать / разрешить автообновления |
| `claudeguard set-model <model>` | Задать модель по умолчанию (например `claude-opus-4-8`) |
| `claudeguard enable-model` / `disable-model` | Включить / выключить подмену модели |
| `claudeguard launch-desktop` | Запустить Claude Desktop с pre-flight проверкой |
| `claudeguard start` / `stop` | Запустить / остановить демон (через systemd) |
| `claudeguard doctor` | Проверить, что все перехваты на месте, и починить |
| `claudeguard set-cli-path <path>` | Указать путь до настоящего `claude` |

### Переустановка Claude нас больше не ломает

Переустановка Claude Code кладёт настоящий бинарник `claude` поверх нашего шима, а
обновление удаляет версионный путь, на который шим передаёт управление, - защита тихо
отваливалась, а демон всё так же показывал "allowed".

Поэтому ClaudeGuard не хардкодит эти пути, а определяет их сам и переподключает всё,
что отвалилось: демон чинит себя сам **каждые 60 с**, обёртка `claude` перевешивает
шимы при каждом запуске, а `/etc/hosts` переприменяется, если его правит кто-то извне.
Посмотреть состояние всех перехватов (и сразу починить) - `claudeguard doctor`.

## Как это работает

1. **STUN-first детект IP** - внешний IP берётся одним UDP-раундтрипом STUN (десятки мс), с откатом на HTTP-сервисы там, где UDP заблокирован.
2. **Мозг** (`src/brain.py`) превращает IP в один вердикт - `allowed` / `blocked` / `offline` - и работает **fail closed**: пускает только подтверждённый `allowed`.
3. **Единый источник истины** - демон пишет вердикт в `~/.config/claudeguard/state.json` на каждой проверке. Обёртка CLI и лаунчер читают его (мгновенно) и делают свою проверку только если демон лёг.
4. **Энфорсмент** - всё семейство доменов Claude/Anthropic (~175 хостов на 5 корневых доменах) блокируется через `/etc/hosts` + правила iptables (TCP + UDP/QUIC); Claude Desktop (Electron) убивается; активная сессия `claude` терминируется в момент, когда IP слетает с whitelist.
5. **Root-хелпер** - systemd path unit следит за триггер-файлом; когда пользовательский демон трогает его, root-сервис применяет подготовленные `/etc/hosts` и iptables-правила. Без sudoers, без setuid.

> **Область применения:** это защита от *случайной* утечки на своей машине, а не от злонамеренного админа.

## Структура проекта

```
src/
  brain.py          Единое ядро решения (читает вердикт демона; STUN-фолбэк)
  ip_checker.py     Детект внешнего IP: STUN по UDP, затем HTTP
  config.py         Конфиг в ~/.config/claudeguard/config.json
  network_guard.py  Блок/разблок через /etc/hosts + iptables
  update_guard.py   Заморозка/разморозка автообновлений Claude
  integrity.py      Ищет реальные пути Claude; переподключает перехваты
  cli_wrapper.py    Pre-flight перехватчик команды `claude`
  app_launcher.py   Pre-flight перехватчик Claude Desktop
  daemon.py         Фоновый демон (systemd): мониторинг, энфорсмент, state-файл
  tray.py           Опциональная иконка в system tray (pystray + Pillow)
  hosts_helper.c    Root-хелпер (systemd): пишет /etc/hosts + iptables
  helper.py         Диспетчер вызовов демон -> хелпер
bin/claudeguard     CLI управления
install.sh          Компилирует нативно и всё устанавливает
uninstall.sh        Чистое удаление, восстанавливает оригинальный `claude`
```

## Лицензия

MIT - см. [LICENSE](LICENSE).
