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

### Protected device storage indexing

For **Index device storage** from another device, start the phone server with HTTPS. Use a PEM certificate trusted by the phone and valid for the Wi-Fi IP address, plus its matching private key on the PC. Enter the paths to your own files.

```powershell
$echoAddress = Read-Host "PC Wi-Fi IPv4 address"
$echoCert = Read-Host "Path to the trusted certificate PEM"
$echoKey = Read-Host "Path to its private key PEM"
.\.venv\Scripts\python.exe -m echo.phone $echoAddress --port 8001 --ssl-certfile $echoCert --ssl-keyfile $echoKey
```

Open the printed HTTPS address on the phone. The HTTP phone launcher remains available for ordinary use on trusted Wi-Fi; traffic on that connection is unencrypted. On the host PC, the storage picker also works through the localhost URL.

#### Set up a local certificate for your own phone

If you do not already have a certificate, install [mkcert for Windows](https://github.com/FiloSottile/mkcert#installation) on the PC. From the ECHO project folder, generate a certificate for the PC's current Wi-Fi IP and start ECHO.

```powershell
$echoAddress = Read-Host "PC Wi-Fi IPv4 address"
New-Item -ItemType Directory -Force .echo\tls | Out-Null
mkcert -cert-file .echo\tls\phone.pem -key-file .echo\tls\phone-key.pem $echoAddress
mkcert -CAROOT
```

Copy only `rootCA.pem` from the folder printed by `mkcert -CAROOT` to your phone. Keep both private keys on the PC. On Android, select **CA certificate** under the device's certificate installation settings. On iPhone, install the certificate profile and enable it under **Settings → General → About → Certificate Trust Settings**. Use a certificate authority you created and control. [mkcert mobile instructions](https://github.com/FiloSottile/mkcert#mobile-devices), [Apple certificate settings](https://support.apple.com/en-us/102390)

```powershell
.\.venv\Scripts\python.exe -m echo.phone $echoAddress --port 8001 --ssl-certfile .echo\tls\phone.pem --ssl-keyfile .echo\tls\phone-key.pem
```

Open the printed HTTPS URL. If your PC's IP changes, generate a certificate for the new IP and relaunch.

#### Select a folder or ZIP

Open **Settings → Index device storage**. Choose **Choose folder** if your phone's picker allows selecting a whole directory. If the picker only opens it, compress the folder in your phone's Files app and choose **Choose ZIP**. You can also choose multiple individual files. The ZIP is read on the phone. Review its list, select the files you want, then choose **Allow and index selected files**. ECHO sends only those selected files and stores their searchable text in your account.
