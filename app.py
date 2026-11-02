from urllib.parse import quote

import streamlit as st

from main import triage_issue


# Streamlit has no setting for its "running" icon (the
# animated swimmer/rower/cyclist in the top-right), so
# hide it with CSS and show the Sentry glyph instead.
SENTRY_GLYPH = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path fill="#362D59" d="M13.91 2.505c-.873-1.448-2.972-1.448'
    "-3.844 0L6.904 7.92a15.478 15.478 0 0 1 8.53 12.811h-2.221A13"
    ".301 13.301 0 0 0 5.784 9.814l-2.926 5.06a7.65 7.65 0 0 1 4.435"
    " 5.848H2.194a.365.365 0 0 1-.298-.534l1.413-2.402a5.16 5.16 0 0"
    " 0-1.614-.913L.296 19.275a2.182 2.182 0 0 0 .812 2.999 2.24 2.24"
    " 0 0 0 1.086.288h6.983a9.322 9.322 0 0 0-3.845-8.318l1.11-1.922"
    "a11.47 11.47 0 0 1 4.95 10.24h5.915a17.242 17.242 0 0 0-7.885"
    "-15.28l2.244-3.845a.37.37 0 0 1 .504-.13c.255.14 9.75 16.708"
    " 9.928 16.9a.365.365 0 0 1-.327.543h-2.287c.029.612.029 1.223"
    ' 0 1.831h2.297a2.206 2.206 0 0 0 1.922-3.31z"/></svg>'
)

st.html(f"""
<style>
[data-testid="stStatusWidgetRunningIcon"] {{
    background: url("data:image/svg+xml,{quote(SENTRY_GLYPH)}")
        center / contain no-repeat;
    animation: sentry-pulse 1.2s ease-in-out infinite;
}}

[data-testid="stStatusWidgetRunningIcon"] > * {{
    visibility: hidden;
}}

@keyframes sentry-pulse {{
    50% {{ opacity: 0.35; }}
}}
</style>
""")


st.title("Sentry Support Issue Triage")

st.write("Paste a GitHub issue from the Support triage queue.")

issue_url = st.text_input(
    "GitHub issue URL",
    placeholder="https://github.com/getsentry/sentry/issues/12345",
)

look_at_code = st.checkbox(
    "Also look at the code for a suspected cause",
    help=(
        "Off: code is only read when the agent cannot "
        "choose between Product Areas. On: always read "
        "code (several times the token cost)."
    ),
)

if st.button("Triage issue"):

    if not issue_url:
        st.warning("Paste a GitHub issue URL first.")

    else:
        with st.spinner("Agent is triaging the issue..."):
            try:
                result = triage_issue(
                    issue_url,
                    force_code_lookup=look_at_code,
                )

                st.subheader("Triage Recommendation")
                st.markdown(result)

            except Exception as e:
                st.error(f"Triage failed: {e}")