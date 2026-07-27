# Aura WhatsApp Cloud API — Railway configuration

Aura sends consultation confirmations and advisor booking alerts through Meta's
WhatsApp Cloud API. Configure the following variables on the **Aura web service**
in Railway's production environment.

## Required variables

```text
WHATSAPP_TOKEN=<Meta permanent or system-user access token>
WHATSAPP_PHONE_NUMBER_ID=<Meta Phone Number ID>
WHATSAPP_ADMIN_PHONE_NUMBER=<advisor number in international digits>
```

`WHATSAPP_PHONE_NUMBER_ID` is the numeric **Phone Number ID** shown in Meta's
WhatsApp API setup. It is not the visible WhatsApp telephone number and it is not
the WhatsApp Business Account ID.

Use international digits for `WHATSAPP_ADMIN_PHONE_NUMBER`, for example:

```text
2348012345678
```

Do not include spaces, brackets or a leading `+`. Aura normalises common phone
formatting, but storing the canonical international number avoids ambiguity.

## Optional variables

```text
WHATSAPP_GRAPH_API_VERSION=v23.0
WHATSAPP_ADMIN_TEMPLATE=admin_booking_alert_v1
WHATSAPP_BOOKING_TEMPLATE=booking_confirmation
WHATSAPP_TEMPLATE_LANGUAGE=en
WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=0
```

Aura defaults to `v23.0`, `admin_booking_alert_v1`, `booking_confirmation` and
English when the optional values are absent.

Keep `WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=0` when the approved admin template
contains no body placeholders. Set it to `1` only when the approved template body
contains three placeholders in this exact order:

1. Client name
2. Vehicle
3. Preferred consultation time

## Deployment check

After saving Railway variables, wait for the resulting deployment to become
**Active**, then create a fresh consultation booking. A successful Meta response
is logged as:

```text
WhatsApp message accepted by Meta
```

Aura deliberately does not log access tokens, complete outbound payloads or
recipient numbers.

When configuration is incomplete, Aura skips the provider call and logs the
specific missing Railway variable instead of sending a malformed request such as
`/None/messages`.
