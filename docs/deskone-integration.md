# Інтеграція з DeskOne

Прошивка та інструменти розробки підтримуються в Kubik-ESP32.
Власник персонажа й усіх правил — DeskOne.

У DeskOne вже підготовлено [чернетку PR №32](https://github.com/MaanAndrii/DeskOne/pull/32)
на основі main `6308bc00c8db8eebe158f4c5a9dd233e90f1fc95`.
Він не злитий із main. Для першої перевірки можна запустити цю гілку.
Не застосовуйте наведені вставки вдруге до гілки, де bridge вже встановлено.

## Для подальшої інтеграції

Скопіюйте `companion/bridge.py` цього репозиторію до
`services/esp32_bridge.py` у DeskOne. У app.py:

1. Додайте імпорт:
```python
from services.esp32_bridge import CompanionBridge
```
2. Перед `running = True` створіть сервіс:
```python
companion = CompanionBridge.from_environment()
```
3. Після оновлення schedule, pet і behavior, перед циклом pygame.event.get:
```python
if companion is not None:
    for _ in companion.drain():
        if pet.is_sleeping:
            behavior.trigger("SURPRISED", 1.0, "TOUCHED_DURING_SLEEP")
        elif not quiet_policy.quiet:
            reaction = behavior.handle_interaction(pet, "touch")
            play_reaction_sound(reaction)
```
4. Безпосередньо після отримання реального `scene_frame = live_frame(...)`,
   до заміни кадру діагностичним preview:
```python
if companion is not None:
    companion.publish(scene_frame, schedule.mode, pet.is_sleeping)
```
5. У cleanup, перед збереженням персонажа:
```python
if companion is not None:
    companion.close()
```

Після адаптації запустіть тести DeskOne. Сервер має отримувати тільки живий
вирішений SceneFrame; preview не повинен змінювати стан плати.
Мережеві потоки не викликають методи персонажа — тільки ставлять події в чергу.

## Увімкнення

У PowerShell у каталозі DeskOne:
```powershell
$env:KUBIK_BRIDGE_ENABLE = "1"
$env:KUBIK_BRIDGE_TOKEN = "<власний випадковий токен, мінімум 24 символи>"
python app.py
```
Деталі мережі й прошивки: [kubik-esp32.md](kubik-esp32.md).
