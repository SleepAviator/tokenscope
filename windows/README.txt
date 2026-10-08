TokenScope for Windows x64

Double-click launch-tokenscope.bat. It starts the dashboard, opens your browser,
and leaves this console open so collection progress is visible. Press Ctrl+C in
the console to stop TokenScope and its active collection.

Edit your private machine settings at:
%APPDATA%\TokenScope\config.ini

Dashboard snapshots stay in RAM and are cleared when TokenScope stops.
Refreshes create no cache files or usage exports; source inputs are read-only.

By default, the dashboard is available to devices on your trusted LAN and has no
login or TLS. Do not expose it to the public Internet.
