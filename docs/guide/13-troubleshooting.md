# Troubleshooting

**Windows says the app is unsafe.** The app is not signed. Click **More info → Run anyway**.

**Nothing plays.** Check the volume sliders in the mixer, then check that a track is selected. If you use a playlist or a genre,
make sure the source files still exist. Missing playlist tracks show up grey. Press **🔄 RELOAD (Ctrl+R)** after changing folders.

**The waveform is empty.** The waveform needs a moment to be calculated, and only works for files the tool can read.
It is a display only, so playback is not affected.

**The projector shows on the wrong screen.** It opens on the screen that is *not* your main screen. Set your projector as the second display
in Windows (Win+P → *Extend*) before you press the button.

**My layout shows a missing picture.** The picture moved or was renamed. Put it back into the scene's `visuals` folder (or into your visuals library).

**The recording has no sound.** Start a new recording and pick the correct **Input device** in the setup window.

**The transcript has wrong names, is slow, or mixes up speakers.** See the problem list at the end of [Transcribing your session](14-transcription.md).

**It started and crashed.** Look for `crash.log` next to the program. It tells what happened. If you report an issue on GitHub, attach the file
(check first that it holds nothing private).

**I reset everything I set.** Your paths are stored in `director_config.json` next to the program.
If you delete that file, the tool starts fresh and you set your folders again.
