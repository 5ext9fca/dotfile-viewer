# shellcheck shell=bash
# Native actions stay in their owning shell; lease decisions belong to Python.
deployment_run() (
  local dry="$1" lease path token owned
  shift
  if [[ "$dry" == 1 ]];then "$@";exit;fi
  dotfiles_python_init || exit
  lease="$("$dotfiles_python_executable" -m dotfile_viewer.lease acquire --tsv --owner-pid "$$")" || exit "$?"
  IFS=$'\t' read -r path token owned <<< "$lease"
  if [[ "$owned" == 1 ]];then
    # shellcheck disable=SC2064
    trap "$(printf '%q -m dotfile_viewer.lease release --path %q --token %q' "$dotfiles_python_executable" "$path" "$token")" EXIT
  fi
  export DOTFILES_DEPLOYMENT_LOCK="$path" DOTFILES_DEPLOYMENT_TOKEN="$token"
  "$@"
)
