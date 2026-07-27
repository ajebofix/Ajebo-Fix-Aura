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

## Template variables

Aura's currently approved fixed-text admin template is:

```text
WHATSAPP_ADMIN_TEMPLATE=admin_booking_alert
WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=0
```

Do not point production at `admin_booking_alert_v1` unless that exact template is
approved in Meta and its body parameters match Aura's payload.

The client-facing booking template defaults to:

```text
WHATSAPP_BOOKING_TEMPLATE=booking_confirmation
```

The remaining optional settings are:

```text
WHATSAPP_GRAPH_API_VERSION=v23.0
WHATSAPP_TEMPLATE_LANGUAGE=en
WHATSAPP_ADMIN_TEMPLATE_LANGUAGE=en
WHATSAPP_BOOKING_TEMPLATE_LANGUAGE=en
```

The scoped language values are optional. When absent, both templates use
`WHATSAPP_TEMPLATE_LANGUAGE`, which itself defaults to `en`. Set a scoped value
only when the corresponding approved Meta template uses a different language
code, such as `en_US` or `en_GB`.

Keep `WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=0` while the approved admin template
contains no body placeholders. Set it to `1` only when the selected approved
admin template body contains three placeholders in this exact order:

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

When Meta rejects a request, Aura records only safe diagnostic fields: HTTP
status, provider error code, subcode, trace ID, compact error message and compact
`error_data.details`. These fields are sufficient to identify template-language,
parameter-count and payload-validation failures without exposing credentials or
message recipients.

When configuration is incomplete, Aura skips the provider call and logs the
specific missing Railway variable instead of sending a malformed request such as
`/None/messages`.
