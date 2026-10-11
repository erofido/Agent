# Task Tracker

A small Expo app to keep track of things you need to do, how long each one took, and whether it's done.

## Features

- Add tasks, then tap the circle to mark one done (tap again to undo).
- Press ▶ to start a timer on a task and ❚❚ to pause it. Marking a task done stops its timer.
- Tap a task to rename it, set the time by hand (hours/minutes), or delete it.
- Filter by **To do**, **Done** or **All**. The header shows how many are done and the total time tracked.
- Everything is saved on the device (AsyncStorage), so it's still there after you close the app.

## Run it

```bash
cd task-tracker
npm install
npx expo start
```

Then scan the QR code with the **Expo Go** app on your phone, or press `w` to open it in a browser.
