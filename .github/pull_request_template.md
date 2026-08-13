**What this changes, and why**

**How you checked it**
<!-- For anything touching the writer: did you build a file and OPEN it in Designer?
     A green run is not the same as a file Designer will load. -->

- [ ] **Opened the app in a browser and used the step you changed.** The API being
      healthy proves nothing about the page: the whole interface is one script, and a
      syntax error in it leaves a dead app that still looks alive. That shipped once.
- [ ] Built a `.hw` and opened it in Lutron Designer
- [ ] If it touches keypad models: verified against **Reports → Bill of Materials**,
      which prints the real Lutron part number (a wrong ID builds the wrong hardware
      silently — `inspect` cannot catch it)
- [ ] No client data in the diff — no `.hw`, no real schedules, no room names from a
      live job
