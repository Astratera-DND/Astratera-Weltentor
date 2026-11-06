# Getting started

## Download and start

1. Go to the **Releases** page of the project on GitHub and download `Astratera-Weltentor-win64.zip`.
2. Unzip it anywhere you like (a USB stick works too).
3. Double-click `Astratera-Weltentor.exe`. The first start can take a few seconds.

You don't need to install anything else. The audio player (VLC) is already inside.

**Windows warns me!** The app is not signed, so Windows SmartScreen or your virus scanner may say it doesn't know it.
Click **More info → Run anyway**. The whole source code is public if you want to look inside.

## Try the demo project first

The zip also contains `sample_project` and `sample_libraries`. Everything in there is made up (generated sounds and
pictures), so you can click around without risk.

1. At the top, click **📁 Browse…** and choose the `sample_project` folder.
   Pick the folder that *contains* the session folders, not the session itself.
2. Click **Demo Session** in the left list, then the scene **The Tavern**.
3. You'll see music, sound effects, visuals and layouts appear in the panels.
4. Double-click **Tavern + NPC.json** in the Layouts list. The picture fades in on the stage.
5. Press **🌟 START ALL** (bottom right) to start layout, music and sound together.

## Point the tool at your own campaign

Create a folder for your campaign (for example `D:\Campaigns\Sessions`) and select it with **📁 Browse…**.
Then press the **+** next to *Sessions* to make a session, and the **+** next to *Scenes* to make a scene.
The tool creates the folders for you. More about that in [Sessions, scenes and your files](03-sessions-scenes-files.md).

## Your libraries

Besides the files inside a scene, you can have big shared collections of music, sounds and pictures, for example
one music folder sorted by genre. You tell the tool where they are once (the little **…** buttons in the panels), and
it remembers. These settings are saved on your PC in `director_config.json`, next to the program.
