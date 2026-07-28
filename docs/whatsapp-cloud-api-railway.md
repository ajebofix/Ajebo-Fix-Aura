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

## Production template variables

Meta's production responses confirm that Aura's available admin template is
`admin_booking_alert_v1` and that its body expects three values. Configure:

```text
WHATSAPP_ADMIN_TEMPLATE=admin_booking_alert_v1
WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=1
WHATSAPP_ADMIN_TEMPLATE_LANGUAGE=en
```

Aura sends the admin parameters in this order:

1. Client name
2. Vehicle
3. Preferred consultation time

The client-facing booking template is:

```text
WHATSAPP_BOOKING_TEMPLATE=booking_confirmation
WHATSAPP_BOOKING_TEMPLATE_LANGUAGE=en
```

Aura sends two client body values in this order:

1. Client name
2. Vehicle

## Positional versus named template variables

By default, Aura sends positional body parameters. This matches templates created
with numbered placeholders such as `{{1}}`, `{{2}}` and `{{3}}`.

When a Meta template uses named placeholders, configure the exact approved names
as comma-separated Railway values. Example:

```text
WHATSAPP_BOOKING_TEMPLATE_PARAMETER_NAMES=client_name,vehicle_name
WHATSAPP_ADMIN_TEMPLATE_PARAMETER_NAMES=client_name,vehicle_name,preferred_time
```

Do not guess these names. Copy them from the approved template in WhatsApp
Manager. Aura refuses to call Meta when the configured name count does not match
the number of values the selected workflow sends.

## Remaining optional settings

```text
WHATSAPP_GRAPH_API_VERSION=v23.0
WHATSAPP_TEMPLATE_LANGUAGE=en
```

The scoped language values override `WHATSAPP_TEMPLATE_LANGUAGE`. The language
code must exactly match the approved translation in Meta. For example, a template
approved as `en_US` will not necessarily be available as `en`.

A different fixed-text admin template can still be used by setting:

```text
WHATSAPP_ADMIN_TEMPLATE=<approved-template-name>
WHATSAPP_ADMIN_TEMPLATE_USES_PARAMETERS=0
```

Only do this when that exact template and language appear as approved in
WhatsApp Manager.

## Deployment check

After saving Railway variables, wait for the resulting deployment to become
**Active**, then create a fresh consultation booking. A successful Meta response
is logged as:

```text
WhatsApp message accepted by Meta
```

The success or rejection log also records a safe message contract:

```text
template=<name>
language=<code>
body_parameters=<count>
named_parameters=<count>
recipient_digits=<length>
```

Aura deliberately does not log access tokens, complete outbound payloads,
parameter values or recipient numbers.

When Meta rejects a request, Aura records only safe diagnostic fields: HTTP
status, provider error code, subcode, trace ID, compact error message,
`error_data.details` and the safe contract above. These fields identify
language, name, parameter-style and parameter-count failures without exposing
credentials or personal message content.

When configuration is incomplete, Aura skips the provider call and logs the
specific missing Railway variable instead of sending a malformed request such as
`/None/messages`.
