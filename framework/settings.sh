# Sourced, not run: the lab's own settings, as environment variables.
#
#   lab.yaml                what this lab runs and where its things are (see the
#                           template beside it; docs/settings.md explains each)
#   notifiers/<name>/       one transport: how a message leaves the lab, and the
#                           settings for it -- <name>.env, default notifiers/slack/
#
# Both are untracked and both are optional. Anything already in the environment wins,
# so a campaign's run.sh has the last word on what the lab merely offers.
_settings_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$_settings_root/framework/lab_config.py" --export 2>/dev/null)"
# The lab's environment, put in place before anything needs an interpreter. None of
# this runs in a terminal, so nothing a person's shell startup does is in effect here;
# harmless when it is already set up, since activating twice is what a second terminal
# does.
#
# `conda activate` is a shell function that only exists once conda.sh has been sourced,
# which for a person is their startup file's business. Writing that line too is easy to
# forget and the error it gives -- "Run 'conda init' before 'conda activate'" -- does
# not say so, hence sourcing it here from wherever the conda on PATH lives.
if [ -n "${LAB_ACTIVATE:-}" ]; then
    case "$LAB_ACTIVATE" in
        *conda\ activate*)
            if [ "$(type -t conda || true)" != "function" ] && command -v conda >/dev/null 2>&1; then
                _settings_conda="$(dirname "$(dirname "$(command -v conda)")")/etc/profile.d/conda.sh"
                [ -f "$_settings_conda" ] && . "$_settings_conda"
                unset _settings_conda
            fi ;;
    esac
    eval "$LAB_ACTIVATE"
fi

_settings_name="${NOTIFIER:-slack}"
_settings_notifier="$_settings_root/notifiers/$_settings_name/$_settings_name.env"
# Labs set up before the transports became directories keep their settings beside them.
[ -f "$_settings_notifier" ] || _settings_notifier="$_settings_root/notifiers/$_settings_name.env"
# Exported while sourced: the notifier file is written as plain assignments, and what
# a lab configures has to reach the Python these scripts start, not just the shell.
if [ -f "$_settings_notifier" ]; then
    set -a
    . "$_settings_notifier"
    set +a
fi
unset _settings_root _settings_name _settings_notifier
