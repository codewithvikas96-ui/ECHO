# Open ECHO on a phone

Connect the phone and the PC to the same trusted Wi-Fi network. Run `ipconfig` on the PC and find the **IPv4 Address** under the active Wi-Fi adapter.

From PowerShell in the ECHO project folder, enter your PC's address at the prompt.

```powershell
$echoAddress = Read-Host "PC Wi-Fi IPv4 address"
.\.venv\Scripts\python.exe -m echo.phone $echoAddress --port 8001
```

1. Open the URL printed in the terminal on the phone. Use the gateway username and password printed beside it.
2. At the ECHO sign-in screen, use your personal account to open your own library. Using the same account on your PC and phone gives you access to the same sources.
3. On a shared device, choose **Settings → Sign out** when finished. Press **Ctrl+C** in the PC terminal to stop the phone server.

If Windows reports error **10048**, that address and port are already in use. Run the command with another port, such as `--port 8010`, and open the newly printed URL. If the browser cannot connect, check that it is using the PC's Wi-Fi address and that the server terminal is still open.
