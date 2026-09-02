"""Release feature gates that must be explicit in packaged builds."""

# Foreground-app-based Prompt/dictation routing remains experimental. Keep the
# implementation available for development, but do not let release builds
# change the user's configured input mode until the product definition settles.
ADAPTIVE_INPUT_MODE_ENABLED = False
