# Manual verification — scheduled-job removal (rev. 5 build)

Everything below is what the automated suites (Python 14, Node 10, Qt fallback 17, native-path
interactive 30, HTTP serve-surface interactive 21) already prove for the extracted tree. This
checklist exists so a human can confirm the same behaviour in the **delivered AppImage**, with the
exact strings the app is expected to show, so a manual pass cannot be ambiguous.

Run with no installation:

    chmod +x Tihulu_Satellite_Center-0.3.0-job-removal-r5-x86_64.AppImage
    ./Tihulu_Satellite_Center-0.3.0-job-removal-r5-x86_64.AppImage

Before starting, record the installed version so you can prove it was not replaced:

    sha256sum /usr/bin/tihulu-satellite-center
    find /usr/share/tihulu-satellite-center -type f | wc -l     # reference: 81 files

The AppImage writes nothing outside its own runtime state; the installed copy must not change.

## 1. Native desktop window (default mode)

1. Arm two records that will actually fire: one `pending` (Receiver & pass planner → Auto
   reception), and let one earlier capture finish so it becomes `completed`.
2. The armed-jobs list shows a per-card **×** on *finished* records only.
   - A `running` card ("… · RUNNING" in its heading) has **no ×** — nothing may be removed from a
     capture in progress.
   - The `pending` card's × cancels the schedule (it is labelled "Cancel this pending schedule …"
     in its tooltip); it never silently drops a reservation.
3. With at least one finished record present, the **Clear N finished job(s)** bar appears.
   - Clicking it first asks: *"Remove N finished record(s) from the list and the native
     calendar? Pending and running schedules are never dropped."* — choose **Cancel** and confirm
     nothing changed.
   - Choose **OK**: the finished records disappear from the list **and** from the native reception
     calendar; the pending/running ones stay.
   - If the removal fails, the message appears in the readiness line and the × stays clickable
     (no dead button).
4. On the calendar itself, a finished record's card offers **Remove record**; a pending record's
   card offers only *Edit time / duration / protocol* and *Cancel job*.
   - Remove one from the calendar: it disappears from the calendar **and** the armed-jobs list.

## 2. HTTP serve mode

    ./Tihulu_Satellite_Center-0.3.0-job-removal-r5-x86_64.AppImage serve --open-browser

1. Open `http://127.0.0.1:8791/browser/reception.html` (the app's own port; on any other port the
   companion controls are intentionally inert).
2. With a finished record on the shown day, its calendar card offers **Remove record**; clicking it
   reports *"Removed the finished record for <satellite>."* and the record leaves the calendar.
3. **Clear finished records** asks first: *"Remove N finished record(s)? Pending and running
   schedules stay."* — Cancel leaves everything; OK reports *"Cleared N finished record(s); M
   schedule(s) remain."*
4. A pending record can never be removed: its card has no removal control, and a direct API call
   is refused with *"A pending schedule cannot be cleared: wait for it to finish, or stop it
   first"*.

## 3. Qt-widgets desktop fallback

    ./Tihulu_Satellite_Center-0.3.0-job-removal-r5-x86_64.AppImage desktop-widgets

1. In the reception area, next to the existing **Cancel selected pending job** and
   **Edit selected automatic job**, the new **Remove selected record** and
   **Clear finished records** buttons exist.
2. Select a finished record → **Remove selected record**: the record leaves the list *and* the
   calendar, and the note under the buttons reports
   *"Removed 1 finished record(s); 1 schedule(s) still listed."* — this message must **stay
   visible** after the calendar repaints (it is written to its own label, not the timeline caption).
3. Select a pending record → **Remove selected record**: it is refused with
   *"A pending schedule cannot be removed: wait for it to finish, or stop it first."* and the
   schedule stays.
4. With nothing selected: *"Select a finished record to remove first. Pending and running
   schedules stay until they finish."* — no crash.
5. **Clear finished records** asks first (listing the terminal states and promising pending/running
   are always kept); Cancel changes nothing, OK reports the counts.
6. If the backend refuses (for example the record changed state between listing and clicking), the
   reason is shown in the status bar **and** a warning dialog — never swallowed.

## 4. Preservation checks after testing

    sha256sum /usr/bin/tihulu-satellite-center                      # unchanged
    find /usr/share/tihulu-satellite-center -type f | wc -l         # still 81 files
    ls /usr/share/tihulu-satellite-center/satellite_center/browser/ | grep -c job_removal
    # 0 — the installed copy is the older frozen build and was never touched

Recording the result: note the revision tested (`r5`, digest
`0d2ce55e83d858f36267fa2e201b5519bd5727fef61a6cedeaa48871b5bc857c`), which sections passed, and any
string that differed from the ones above — the automated suites assert these exact strings, so a
difference is a finding, not a cosmetic issue.
