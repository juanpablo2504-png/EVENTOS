# Eventos & Boletos 🎟️

Plataforma de reserva de boletos para eventos (conciertos, partidos, etc.) con flujo completo de aprobación.

## Cómo correrla

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Contraseña de administrador
Por defecto: `admin123` — cámbiala en Administración → Seguridad.

## Configuración del correo
Agrega en Secrets de Streamlit:
```toml
[email]
remitente = "tucorreo@gmail.com"
password = "xxxx xxxx xxxx xxxx"
```

## Flujo completo
1. Admin crea un evento (nombre, fecha, venue, imagen)
2. Admin agrega tipos de boleto con su capacidad (Gradas: 30, Palco: 20, VIP: 10)
3. Admin publica el evento
4. El público ve los eventos como tarjetas, elige un tipo y solicita boletos
5. El desglose inicial pregunta para quién son (configurable)
6. La persona recibe un código de reserva y confirmación por correo
7. Admin aprueba o rechaza desde Administración → Solicitudes pendientes
8. Al aprobar: la persona recibe correo y sube la lista de invitados en Excel
9. Dashboard muestra métricas por evento

## Imágenes de eventos
Puedes pegar una URL directa o subir un archivo desde el formulario de eventos.
Las imágenes subidas se guardan en la base de datos como base64.

## Contactos en correos
Configura en Administración → Reglas → Pie de correos.

## URL de la app
Configura en Administración → Reglas → URL de la app para que los correos tengan el enlace correcto.
