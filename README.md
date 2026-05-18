# Sunsor

`Sunsor` is a simple Windows Python app that switches your cursor profile based on time of day.

<img width="1218" height="885" alt="Screenshot 2026-05-18 034742" src="https://github.com/user-attachments/assets/8b23ec86-131c-4caa-9d6d-1e1d4c4c9f8e" />


## What it does

- Uses a day profile and a night profile
- Defaults to `Windows Default White` for day and `Windows Default Dark` for night
- Lets you change the switch times, like `07:00` and `18:00`
- Lets you choose timezone mode:
  `Auto detect`, `PC local clock`, or a specific timezone like `Europe/Bucharest`
- Lets you create custom cursor profiles and edit them
- Saves your settings in `sunsor_settings.json`
- Keeps running in the system tray after you hide or close the window

## Run it

```powershell
python sunsor.py
```

## Default behavior

- Day starts at `07:00`
- Night starts at `18:00`
- The app checks every 30 seconds and applies the correct profile automatically
- Closing the window sends Sunsor to the tray so it keeps working in the background

## Notes

- This app is for Windows.
- It uses only Python standard library modules: `tkinter`, `winreg`, `ctypes`, and `zoneinfo`.
- Applying a profile updates the current Windows user cursor settings and refreshes the cursors live.


## More Screenshots

<img width="974" height="639" alt="Screenshot 2026-05-18 034759" src="https://github.com/user-attachments/assets/abae757a-36e0-4461-86c8-cbf779f6f899" />
<img width="897" height="724" alt="Screenshot 2026-05-18 034751" src="https://github.com/user-attachments/assets/1a595191-6b4b-4a68-8ffc-6883cd9547ea" />
