# 💳 Billing — Configuración para Costa Rica

> **Importante:** Esta guía cubre la configuración **manual de cuentas externas** (no código).  
> El proyecto Career OS usa 2Checkout (Verifone) como procesador de pagos principal y SINPE Móvil como método local opcional. Los fondos se retiran vía Payoneer.

---

## 📋 Tabla de contenido

1. [Flujo general de pagos](#1-flujo-general-de-pagos)
2. [2Checkout (Verifone) — Cuenta merchant](#2-2checkout-verifone--cuenta-merchant)
3. [Payoneer — Retiro de fondos](#3-payoneer--retiro-de-fondos)
4. [SINPE Móvil — Pago local (Costa Rica)](#4-sinpe-móvil--pago-local-costa-rica)
5. [Resumen de costos y comisiones](#5-resumen-de-costos-y-comisiones)
6. [Diagrama del flujo completo](#6-diagrama-del-flujo-completo)

---

## 1. Flujo general de pagos

```
Usuario (tarjeta/SINPE)
        │
        ▼
┌───────────────────┐
│  2Checkout        │  ← Procesa el pago (Merchant of Record)
│  (Verifone)       │     Maneja impuestos, compliance, facturación
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  Payoneer         │  ← Recibe los fondos en USD
│  (tu cuenta)      │     Retiro a banco local o tarjeta Payoneer
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  Banco CR         │  ← Fondos disponibles en colones
│  (cuenta local)   │
└───────────────────┘
```

---

## 2. 2Checkout (Verifone) — Cuenta merchant

2Checkout actúa como **Merchant of Record** — esto significa que ellos manejan:
- Facturación electrónica
- Cálculo y retención de impuestos (IVA, VAT)
- Cumplimiento regulatorio internacional
- Chargebacks y disputas

### 2.1 Requisitos previos

| Requisito | Detalle |
|---|---|
| **Sitio web funcional** | Debe tener: Términos y condiciones, Política de privacidad, Política de devoluciones, Información de contacto |
| **Edad** | Mayor de 18 años |
| **Documentos** | Pasaporte o cédula, Comprobante de domicilio (recibo de luz/agua < 3 meses) |
| **Empresa** | Si es persona jurídica: registro mercantil, constitución |
| **2FA** | Autenticación de dos factores obligatoria en el panel |
| **VPN** | **No usar VPN** — regístrate desde tu ubicación real en Costa Rica |

### 2.2 Paso a paso

1. **Ve a** [Verifone/2Checkout](https://www.verifone.com/en/2checkout) y haz clic en **"Sign Up"** o **"Get Started"**

2. **Selecciona tu plan:**
   - **2Sell** — Pagos únicos (recomendado para comenzar)
   - **2Subscribe** — Suscripciones recurrentes
   - **2Monetize** — Ventas digitales

3. **Completa el formulario de registro:**
   - País: **Costa Rica**
   - Moneda: **USD** (recomendado para evitar tipo de cambio)
   - Tipo de negocio: **Individual** o **Company**

4. **Sube los documentos solicitados:**
   - Identificación (cédula/pasaporte)
   - Comprobante de domicilio
   - Documentación del negocio (si aplica)

5. **Configura tu sitio web:**
   - Agrega URLs de tu sitio
   - Políticas de privacidad y términos
   - Descripción precisa del producto/servicio

6. **Espera la aprobación:**
   - 2Checkout revisa tu solicitud (2 días a 2 semanas)
   - Responder rápido a cualquier solicitud de documentos adicionales
   - **No apresures el proceso** — una solicitud rechazada es difícil de reconsiderar

7. **Habilita 2FA** en el panel de control (obligatorio)

### 2.3 Integración técnica (para referencia)

> Esto lo configura el desarrollador, no el usuario final.

El proyecto usa la API REST de 2Checkout:
- **Checkout de línea** — Tokenización directa desde el frontend
- **Webhooks** — Notificaciones de pago exitoso/cancelado
- **Instant Payment Notification (IPN)** — Confirmación de estado

---

## 3. Payoneer — Retiro de fondos

Payoneer permite recibir los pagos de 2Checkout y retirarlos a tu banco local en Costa Rica.

### 3.1 Paso a paso

1. **Crea una cuenta Payoneer:**
   - Ve a [payoneer.com](https://www.payoneer.com)
   - Regístrate como **"Individual"** o **"Company"**
   - Usa tus datos reales de Costa Rica

2. **Verifica tu identidad:**
   - Sube cédula o pasaporte
   - Comprobante de domicilio

3. **Solicita tu cuenta receptora en USD:**
   - Payoneer te asigna datos bancarios en EE.UU.
   - Estos datos los usarás en 2Checkout como método de retiro
   - Recibirás un **ABA routing number** y **account number**

4. **Vincula Payoneer con 2Checkout:**

   **Opción A — Tarjeta 2Checkout MasterCard (recomendada):**
   - En el panel de 2Checkout ve a **Settings → Payout details**
   - Busca **"2Checkout MasterCard powered by Payoneer"**
   - Sigue el flujo de vinculación
   - Serás redirigido a Payoneer para autorizar

   **Opción B — Wire Transfer a cuenta receptora Payoneer:**
   - En el panel de 2Checkout ve a **Settings → Payout details**
   - Selecciona **"Wire Transfer"**
   - Ingresa los datos de tu cuenta receptora Payoneer:
     - Bank Name: (el banco asignado por Payoneer)
     - Account Number: (el número asignado)
     - ABA/Routing: (el routing number asignado)

5. **Espera la aprobación:**
   - Cualquier cambio en payout details requiere revisión manual
   - Hasta **2 días hábiles** para aprobación

### 3.2 Retirar a banco costarricense

1. En Payoneer, ve a **Withdraw → Bank Account**
2. Agrega tu cuenta bancaria en Costa Rica:
   - Banco: BAC / BNCR / Promérica (los que soportan transferencias internacionales)
   - SWIFT/BIC Code
   - Número de cuenta en colones
3. Solicita la transferencia (tarda 1-3 días hábiles)
4. Payoneer convierte USD → CRC con su tasa de cambio

### 3.3 Costos de Payoneer

| Concepto | Costo |
|---|---|
| Mantenimiento de cuenta | Gratis (sin actividad anual: ~$29.95) |
| Transferencia a banco local | 1–2% del monto |
| Tipo de cambio | Spread sobre tasa de mercado (~2-3%) |
| Retiro en cajero (tarjeta) | $3.15 por retiro |
| Recepción de fondos 2Checkout | Varía según el plan de 2Checkout |

---

## 4. SINPE Móvil — Pago local (Costa Rica)

SINPE Móvil no tiene API pública. La integración en Career OS es **manual**: el usuario ingresa un número de referencia y el administrador verifica.

### 4.1 Requisitos

1. **Cuenta bancaria en colones** (BAC Credomatic, Banco Nacional, etc.)
2. **Línea telefónica móvil** (a tu nombre o de la empresa)
3. **Afiliación a SINPE Móvil** desde la banca en línea

### 4.2 Paso a paso para afiliar SINPE Móvil

1. **Ingresa a la Banca en Línea** de tu banco
2. Busca **"SINPE Móvil"** en el menú
3. Selecciona **"Afiliar"** o **"Asociar número"**
4. Ingresa tu número de teléfono móvil
5. Completa la verificación de identidad (token, código SMS)
6. **Importante:** Si el número ya estaba asociado a otra cuenta, primero debes desafiarlo:
   - Envía un SMS con la palabra `INACTIVE` al número de servicio del banco anterior
   - O haz el trámite desde la banca en línea del banco anterior

4.3 Aumentar límites de transferencia

- Desde la app móvil o banca web de tu banco
- Busca la opción **"Límites SINPE Móvil"**
- Aumenta el límite diario según tu volumen de negocio
- Los límites seguros suelen ser ₡100,000–₡500,000 por día

### 4.4 Consideraciones fiscales

- **Facturación electrónica obligatoria** para todos los pagos recibidos
- Código de medio de pago: `"06"` (SINPE Móvil)
- Hacienda puede alertar sobre volumen alto de transacciones no declaradas
- Consulta con un contador sobre cómo registrar correctamente los ingresos

---

## 5. Resumen de costos y comisiones

| Servicio | Comisión por transacción | Costo fijo | Retiro |
|---|---|---|---|
| **2Checkout** | 3.5% – 6% + $0.30–$0.50 | Sin mensualidad en plan básico | Wire: ~$15 por transferencia |
| **Payoneer** | 1–2% por retiro a banco local | $29.95/año si sin actividad | 1–3 días hábiles |
| **SINPE Móvil** | 0% por recibir (la mayoría de bancos) | Mantenimiento de cuenta bancaria | Instantáneo |

---

## 6. Diagrama del flujo completo

```
                    ┌─────────────────────┐
                    │   Usuario final      │
                    │  (tarjeta / SINPE)  │
                    └──────────┬──────────┘
                               │ Pago
                               ▼
┌──────────────────────────────────────────────────┐
│                  2Checkout (Verifone)              │
│  • Procesa el pago con tarjeta                    │
│  • Genera factura electrónica                     │
│  • Retiene impuestos (VAT/IVA)                    │
│  • Maneja chargebacks                             │
│  • Cobra su comisión (3.5-6%)                     │
└───────────────────────┬──────────────────────────┘
                        │ Payout (cada 14/30 días)
                        ▼
┌──────────────────────────────────────────────────┐
│                  Payoneer                         │
│  • Recibe los fondos en USD                      │
│  • Comisión por retiro a banco local (1-2%)       │
└───────────────────────┬──────────────────────────┘
                        │ Withdrawal
                        ▼
┌──────────────────────────────────────────────────┐
│          Banco costarricense (CRC)               │
│  • Fondos disponibles en colones                 │
│  • BAC / BNCR / Promérica                        │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│  SINPE Móvil (opcional, solo CR)                 │
│  • Pago directo sin intermediario                │
│  • Sin comisión por recibir                      │
│  • Verificación manual (referencia)              │
│  • Facturación electrónica obligatoria           │
└──────────────────────────────────────────────────┘
```

---

> ⚠️ **Nota importante:** Las políticas de 2Checkout, Payoneer y los bancos costarricenses pueden cambiar. Verifica siempre la documentación oficial antes de configurar cuentas comerciales.
