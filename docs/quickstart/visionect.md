# Quickstart: Visionect signs

Visionect's e-paper signs (the 32" Place & Play, Joan, the 13" boards) are thin clients. The sign dials a server address stored in its flash on TCP port 11113 and draws whatever framebuffer the server pushes. Normally that server is the Visionect Software Suite (VSS), which needs a paid licence from version 7.7. Tesserae can be that server instead: it listens on 11113 and speaks the sign's protocol directly, through [pyvisionect](https://github.com/schlarpc/pyvisionect). No VSS, no Visionect account, no flashing.

!!! note "Tested on one sign so far"
    A Place & Play 32 (HardwareNameID 8, firmware 6.17.4168) has drawn frames pushed over this protocol by pyvisionect. Joan 6, Joan 13, the 13" Place & Play and the 9.7" boards use the same protocol and are in the catalog, but haven't been tried on hardware yet.

!!! warning "No authentication"
    The protocol has none: anything that can reach port 11113 can claim to be any sign. Keep 11113 on your LAN and don't forward it from the internet. Tesserae limits the damage: while a sign is connected, a second connection claiming its UUID from another address is refused (and shown on the device page), oversized or over-compressed frames are dropped, and so are connections that don't identify within 20 seconds.

## 01 — Point the sign at Tesserae

The sign has no discovery and no network setting you can change remotely. It dials the address in its flash, so pick one:

- **Use the old server's address.** If Tesserae runs on the same host as your old Visionect server, stop VSS so port 11113 is free. The sign reconnects to Tesserae within a minute or two, or at its next check-in. To go back, stop Tesserae's listener and start VSS again.
- **Set the address over USB.** Connect the board's micro-USB port (an FTDI serial console, 115200 8N1) and run `server_tcp_set <tesserae-ip> 11113`, then `flash_save` and `reboot`.

Tesserae must be reachable on 11113:

- **Docker:** the compose file uses host networking on Linux, so nothing extra is needed. With port mappings (macOS, Windows), add `"11113:11113"`.
- **Home Assistant App:** not wired up yet. The App doesn't publish 11113/tcp today, so a sign can't reach Tesserae through it. Run Tesserae elsewhere (Docker or a plain install) for now.
- **Other port:** set `TESSERAE_VISIONECT_PORT` (or `visionect_port` in the app settings) and point the sign at that port instead.
- **Address:** the listener binds the same address as the web server, `TESSERAE_BIND_HOST` (all interfaces unless you set it). If you bind the web server to one address, point the sign at that address.

## 02 — Add the sign in Tesserae

Either way works:

- **Settings → Devices → Add device → Visionect.** Pick the model, give it an id and a name, and leave **Sign UUID** blank to bind the first unknown sign that connects within the next hour. You can also paste the UUID from the sign's label or the old VSS device list. A blank device stops binding signs automatically an hour after you add it (or clear its UUID), so it isn't left open to anything else on the network; a sign that connects later shows up as discovered instead, with its UUID.
- **Let the sign announce itself.** Once one Visionect device exists, Tesserae is listening. Any other sign that connects shows up in the **discovered devices** banner on the Devices page. **Register** creates the device bound to that sign, with the model preselected when its screen size identifies it.

The sign reports its own screen size when it connects, and Tesserae adopts it. A Joan 6 renders at 1024×758 and the 32" at 1440×2560, whichever model you picked. **Rotation** works as for any other device. On the 32", if the image arrives upside down, pick 270°.

The Place & Play 32" has no buttons or touch, so its device page has no Controls section (no button map or touch monitor) and Home Assistant gets no Input entity for it.

The device page shows whether Tesserae is listening and what the sign is doing: connected and showing the latest frame, asleep until a given time, or not connected yet. Tesserae only opens the port while at least one Visionect device exists. If the port is busy (VSS still running), the device page and the Add device tab say so, and Tesserae retries every half minute.

## 03 — Bind a dashboard

Bind a page, schedule or lineup to the sign like any other device. Each frame is rendered at 1440×2560 in 16 greys. The device card's **Picture quality** (dither, contrast) applies, and the frame is sent to the sign as is. A connected sign draws the frame straight away. A sleeping sign gets it at its next check-in, and only the newest frame is sent.

When the sign reconnects already showing the current frame (after a Tesserae restart, say), Tesserae doesn't push it again, so there's no needless redraw.

## 04 — Sleep and battery

Battery, signal, temperature and firmware from each check-in show on the device card, in battery history and in Home Assistant. The device page also shows the sign's own check-in interval and sleep mode. Tesserae only reads those; it doesn't change them.

To save battery overnight, set quiet hours (app-wide or on the device) and turn on **sleep through**. When a window starts, the sign gets the current frame and then a deep-sleep command lasting until the window ends. Nothing can wake a sleeping sign early over the network: a frame pushed during its sleep shows when it wakes.
