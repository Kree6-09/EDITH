# E.D.I.T.H. — Android app

Empaqueta el HUD de E.D.I.T.H. como una app Android nativa (Capacitor), con
reconocimiento de voz y texto-a-voz **nativos** de Android (no dependen del
navegador), cámara en vivo y el mismo panel de chat/registro que la versión
web. La app se conecta a tu propio backend E.D.I.T.H. (el mismo servidor
FastAPI de `../backend`) a través de la red.

## Cómo conseguir la APK

**Opción recomendada — GitHub Actions (ya configurado):**

Cualquier push a `mobile/` dispara el workflow
`.github/workflows/build-android.yml`, que compila una APK de depuración y la
sube como artefacto del run. Para descargarla:

1. Ve a la pestaña **Actions** del repo en GitHub.
2. Abre el run más reciente de **"Build Android APK"**.
3. Descarga el artefacto **`edith-debug-apk`** (contiene `app-debug.apk`).
4. Instálala en tu teléfono (activa "Instalar apps de origenes desconocidos"
   si Android lo pide).

Este entorno de desarrollo no tiene acceso al SDK moderno de Android
(los servidores de Google están bloqueados por la política de red), así que
la compilación real ocurre en los runners de GitHub, que sí tienen internet
completo.

**Opción local (Android Studio):**

```bash
cd mobile
npm install
npm run sync          # compila app.js y sincroniza el proyecto android/
```

Abre `mobile/android` en Android Studio y compila/ejecuta normalmente, o por
línea de comandos:

```bash
cd mobile/android
./gradlew assembleDebug
# APK en android/app/build/outputs/apk/debug/app-debug.apk
```

## Primer uso

1. Instala y abre la app.
2. Al pulsar **START SYSTEM** por primera vez, te pedirá la URL del backend
   (ej. `http://192.168.1.50:8000`) — la IP de la máquina donde corre
   `uvicorn main:app`. Debe ser accesible desde la red del teléfono (misma
   Wi-Fi, VPN, o un servidor con IP pública). Podés cambiarla luego con el
   botón **SERVIDOR** en la barra superior.
3. Concede los permisos de cámara y micrófono cuando Android los solicite.
4. Di "Edith" seguido de un comando, o escribe en la consola.

## Notas técnicas

- El backend ya tiene CORS abierto (`allow_origins=["*"]`), así que acepta
  peticiones desde la app empaquetada sin configuración extra.
- Si tu backend corre en `http://` (sin TLS), la app permite tráfico sin
  cifrar (`usesCleartextTraffic`) — normal para uso en red local. Si lo
  exponés a internet, usa HTTPS.
- El reconocimiento de voz usa el motor nativo de Android
  (`@capacitor-community/speech-recognition`) en vez de la Web Speech API del
  navegador (que no existe dentro de un WebView de Android), y hace un bucle
  de escucha continua igual que la versión web (reinicia tras cada frase).
- La síntesis de voz usa el motor TTS nativo de Android
  (`@capacitor-community/text-to-speech`).
- La cámara sigue usando `getUserMedia` como en la web; Capacitor concede el
  acceso al WebView automáticamente una vez que el permiso nativo de cámara
  fue otorgado.
