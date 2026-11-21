# StreamHub

Control Discord and OBS from your phone, add a Now Playing overlay, and get automatic protection from ear-rape in voice chat.
Everything runs on your own PC. Nothing goes through the cloud, and nobody else can see or use your setup.

**What you get**
- **Phone app:** Discord mute/deafen, volume and mute for each person, OBS scenes, audio mutes, go live/record (with an "are you sure?"), and music controls
- **Ear-rape protection:** if someone in voice gets painfully loud, they're muted *for you only*. Works in any server.
- **OBS overlays:** a Now Playing card (Spotify or any player) and a who's-talking overlay

**You need:** Windows 10/11, Python 3.10+ ([python.org](https://www.python.org/downloads/), tick **Add python.exe to PATH**),
OBS 28 or newer, the Discord desktop app, and a phone on the same Wi-Fi as your PC.

---

## Setup (about 10 minutes)

### 1. Start it
1. Unzip this folder somewhere like **Documents**. Avoid Program Files.
2. Double-click **`start.bat`**. The first time takes about a minute.
3. Double-click **`create_desktop_shortcut.bat`** to get a desktop icon. Optional: **`add_to_startup.bat`** makes it start with Windows.

> StreamHub only works while its window is open. Minimize it, don't close it.

### 2. Let your phone connect
1. Double-click **`allow_phone_firewall.bat`** and click **Yes** on the admin prompt.
2. In Windows, go to **Settings → Network & internet → Wi-Fi → your network**, and set it to **Private**.

### 3. Put the app on your phone
1. On your PC, open **http://localhost:8337/pair** (it opens by itself the first time).
2. Scan the QR code with your phone.
3. **iPhone:** Share → **Add to Home Screen**. **Android:** ⋮ → **Add to Home screen**.

### 4. Connect OBS
1. In OBS: **Tools → WebSocket Server Settings**. Tick **Enable WebSocket server**, then click **Show Connect Info** and copy the password.
2. **Close the StreamHub window.** Double-click **`edit_settings.bat`** and paste the password:
   `"password": "paste-it-here",`. Save and close Notepad.
3. In OBS, add **+ → Application Audio Capture**, name it exactly **`Discord`**, and pick **Discord.exe**.
4. Start StreamHub again. The window should say `password set`, and the **OBS** dot in the app turns green.

> Don't want Discord audio on your stream? Keep the source **unmuted**, because auto-mute needs its meter.
> Instead, untick it from the stream tracks under **Edit → Advanced Audio Properties → Tracks**.

### 5. Connect Discord (one time)
Everyone needs **their own** Discord developer app. It's free and takes 2 minutes.
1. Go to **https://discord.com/developers/applications** → **New Application** → name it `StreamHub` → **Create**.
2. Click **OAuth2** and:
   - copy the **Client ID**
   - click **Reset Secret** and copy the **Client Secret**
   - under **Redirects**, add `http://localhost` and click **Save Changes**
3. **Close StreamHub** → `edit_settings.bat` → paste both into the `"discord"` section → save.
4. With the Discord desktop app open, start StreamHub. Click **Authorize** in the Discord popup. It may be hidden behind other windows.

This uses Discord's official local connection (the same one Stream Deck uses). It's **not** a client mod like Vencord, and it isn't a bot. Nobody in your servers can see it or use it.

### 6. Music
In Spotify, go to **Settings → Display** and turn **on** "Show desktop overlay when using media keys", then skip a song.
StreamHub reads what's playing from Windows, so there's no Spotify login.

### 7. Overlays
In OBS, add **+ → Browser** sources:

| Overlay | URL | Size |
|---|---|---|
| Now Playing | `http://localhost:8337/overlay/nowplaying` | 520 × 140 |
| Who's talking | `http://localhost:8337/overlay/voice` | 320 × 600 |

Options you can add to the URL: Now Playing `?progress=0` `?hide=0` `?side=right` · Voice `?only=speaking` `?self=0` `?layout=row`

### 8. Tune ear-rape protection
In a call, open the app's **Voice** tab and watch the meter while people talk normally.
Drag **Trigger level** so the white line is clearly above their normal peaks (the default is −12 dB).
Tap **★** on friends so they're never auto-muted. If two people talk during a spike, both get muted for the set time.

---

## Updates
When a new version is out, your phone app shows an **Update available** card on the Live tab.
Tap **Update**. StreamHub downloads the new version, restarts and reloads the app in about 10 seconds.
Your settings, Discord login and phone pairing aren't touched.
You can also close StreamHub and run **`update.bat`**. Your version number is shown at the bottom of the Live tab.

## Keep your info private
- **Never share `config.json` or the `data` folder.** They hold your passwords, Discord secret, phone pairing key and Discord login.
- To share StreamHub with someone, send them the **original zip**, not your folder.
- If you think your pairing key leaked, set `"key": ""` in `config.json` and restart. Every phone gets un-paired, and you scan the QR code again.
- On shared Wi-Fi (dorms, school, cafés), close StreamHub when you're not using it.

## Troubleshooting
| Problem | Fix |
|---|---|
| Phone says **Reconnecting** | Run `allow_phone_firewall.bat`, and set your Wi-Fi to **Private**. The phone must be on the same Wi-Fi. |
| Phone says **Not paired** | Delete the home-screen icon, scan the QR from `localhost:8337/pair` again, and re-add it. |
| Settings didn't stick | Close StreamHub **before** editing `config.json`, then start it again. |
| "config.json has a typo" | A quote `"` or comma `,` is missing on the line it names, or the line above. |
| Wrong OBS password | Copy it again from **Show Connect Info**. |
| Discord stuck on Authorize | Look for the popup behind other windows. |
| Discord login failed | Check that `http://localhost` is saved under OAuth2 → Redirects, and that the secret is the newest one. |
| No music showing | Turn on the Spotify setting from step 6. The StreamHub window lists `players Windows sees:`. |
| Can't delete the folder | StreamHub is still running. Close its window (or end **Python** in Task Manager). |

Logs: `data/streamhub.log`
