# Test checklist — installed app, 5-minute sample episode

Use any 5-minute recording of two people (a Zoom/Riverside export with one file per person is ideal;
a single phone video of two people also works). Tick each line.

## A. Install & first launch
- [ ] Installer opens; the one-time security warning is passed as in `INSTALL.md`.
- [ ] App icon appears in the Dock / Desktop + Start menu; double-clicking opens the window within ~10 s.
- [ ] Home screen shows **your** hardware line (CPU cores, RAM, GPU or "no GPU acceleration") and a 90-minute estimate.
- [ ] "One-time download: transcription model" banner → **Download now** → progress bar fills → banner disappears.

## B. Create the episode
- [ ] **New episode**: drag the file(s) onto the drop zone (or *Choose files…*). Each file is listed with a role dropdown.
- [ ] Enter guest name, title, institution; leave preset "Standard episode"; tick *Pause so I can review*.
- [ ] **Edit My Episode** → toast with a time estimate → progress screen.

## C. Processing (walk away test)
- [ ] Stages read in plain English: *Checking your files → Transcribing → Editing → Adding music → Making charts → Building the edit list → Rendering preview*.
- [ ] "about N min left" counts down; **Cancel** button is visible.
- [ ] Minimise the window (or close it): the tray/menu-bar icon stays; the job keeps going (reopen from the icon).
- [ ] The computer does not sleep while it runs (leave it 5 minutes untouched).
- [ ] A desktop notification appears when the preview is ready.
- [ ] **Crash test**: quit the app from the tray mid-transcription, reopen, open the episode, press **Resume** → it continues from the same stage (finished stages show green, nothing restarts from zero).

## D. Review
- [ ] **Preview** tab plays the low-res video: title card with your podcast name/guest → lower-third slides in → camera switches (two files) or subtle punch-ins (one file) → chapter card at a break.
- [ ] **Transcript**: speakers are named (rename boxes work). Click a word → type a fix → Enter → it changes. Select a sentence → **Delete selection** → it shows struck-through.
- [ ] **Cuts & fillers**: untick one "um"; move the pause slider; **Save cuts**.
- [ ] **Chapters**: at least one suggested chapter with a sensible title; edit the title; **Save chapters**.
- [ ] **Music**: pick mood *curious*, **Search free music** → list shows licence badges; a CC BY-NC or ND track (if any) is marked **blocked** with the reason; **Use** on a green one → it appears in the Intro slot with attribution text.
- [ ] **Charts**: the guest's charts show thumbnails with "Source: OpenAlex, retrieved <date>" on each; a chart with no data says *no data* and is unticked. If the guest was ambiguous, the "Which of these is the guest?" picker appears. Add a custom chart from pasted numbers.
- [ ] **Re-make preview** → the changes are visible/audible.

## E. Final render & outputs
- [ ] **Render final video** → progress → notification "finished".
- [ ] **Outputs** tab: QA report shows all **PASS**; description text has chapters + music credit + data source.
- [ ] **Open folder** shows `Output/` with every file below (replace *Title* with your episode title):

| file | check |
|---|---|
| `Title.mp4` | plays in QuickTime/VLC; 1920×1080; sounds even, no clipping; length ≈ raw minus pauses/fillers plus 5 s intro |
| `Title_preview.mp4` | low-res version of the same |
| `Title.srt`, `Title.vtt` | open in a text editor: ≤ 2 lines, ≤ 42 chars per line; drop the .srt onto the MP4 in VLC → words match the speech |
| `Title_transcript.docx`, `Title_transcript.txt` | speaker names, timestamp per paragraph, your corrections present, fillers gone |
| `chapters.txt` | `00:00 Introduction` … (paste into YouTube description) |
| `description.txt` | chapters + music attribution + chart data source |
| `music_attribution.txt` | one line per track (or empty if you used your own files) |
| `Title_thumbnail.jpg` | 1280×720 in your brand colours |
| `charts/*.png`, `charts/*.mp4` | static and 3–6 s animated versions |
| `edit_decision_list.txt` | clip list, removed time, chapters |
| `qa_report.txt` | PASS on every line |
| `Title_captions_burned_in.mp4` | only if the burned-in option was ticked |
| `../Title_outputs.zip` | zip of the folder, next to it |

## F. Settings, Canva, queue, errors
- [ ] **Settings**: change the accent colour, save → next episode's title card uses it. Save a preset "Short clips".
- [ ] **Settings → Canva**: paste client ID/secret → **Connect Canva** → browser approval → badge says *connected*. On an episode, **Send to Canva** → assets appear in your Canva *Uploads* and a thumbnail design opens.
- [ ] **Queue**: create a second episode while the first is rendering → sidebar shows "+1 queued"; it starts automatically after the first.
- [ ] **Error path**: rename one source file while an episode is in *Not started* state, press Start → plain-English message ("can't be found any more…") with a fix, **Show log file** opens `logs/engine.log`.
- [ ] **Help → Check for updates** returns "latest version" (or offers the new one).
