# ~/.bashrc: executed by bash(1) for non-login shells.
# see /usr/share/doc/bash/examples/startup-files (in the package bash-doc)
# for examples

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# don't put duplicate lines or lines starting with space in the history.
# See bash(1) for more options
HISTCONTROL=ignoreboth

# append to the history file, don't overwrite it
shopt -s histappend

# for setting history length see HISTSIZE and HISTFILESIZE in bash(1)
HISTSIZE=1000
HISTFILESIZE=2000

# check the window size after each command and, if necessary,
# update the values of LINES and COLUMNS.
shopt -s checkwinsize

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# make less more friendly for non-text input files, see lesspipe(1)
#[ -x /usr/bin/lesspipe ] && eval "$(SHELL=/bin/sh lesspipe)"

# set variable identifying the chroot you work in (used in the prompt below)
if [ -z "${debian_chroot:-}" ] && [ -r /etc/debian_chroot ]; then
    debian_chroot=$(cat /etc/debian_chroot)
fi

# set a fancy prompt (non-color, unless we know we "want" color)
case "$TERM" in
    xterm-color|*-256color) color_prompt=yes;;
esac

# uncomment for a colored prompt, if the terminal has the capability; turned
# off by default to not distract the user: the focus in a terminal window
# should be on the output of commands, not on the prompt
force_color_prompt=yes

if [ -n "$force_color_prompt" ]; then
    if [ -x /usr/bin/tput ] && tput setaf 1 >&/dev/null; then
	# We have color support; assume it's compliant with Ecma-48
	# (ISO/IEC-6429). (Lack of such support is extremely rare, and such
	# a case would tend to support setf rather than setaf.)
	color_prompt=yes
    else
	color_prompt=
    fi
fi

if [ "$color_prompt" = yes ]; then
    PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w \$\[\033[00m\] '
else
    PS1='${debian_chroot:+($debian_chroot)}\u@\h:\w\$ '
fi
unset color_prompt force_color_prompt

# If this is an xterm set the title to user@host:dir
case "$TERM" in
xterm*|rxvt*)
    PS1="\[\e]0;${debian_chroot:+($debian_chroot)}\u@\h: \w\a\]$PS1"
    ;;
*)
    ;;
esac

# enable color support of ls and also add handy aliases
if [ -x /usr/bin/dircolors ]; then
    test -r ~/.dircolors && eval "$(dircolors -b ~/.dircolors)" || eval "$(dircolors -b)"
    alias ls='ls --color=auto'
    #alias dir='dir --color=auto'
    #alias vdir='vdir --color=auto'

    alias grep='grep --color=auto'
    alias fgrep='fgrep --color=auto'
    alias egrep='egrep --color=auto'
fi

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# some more ls aliases
#alias ll='ls -l'
#alias la='ls -A'
#alias l='ls -CF'

# Alias definitions.
# You may want to put all your additions into a separate file like
# ~/.bash_aliases, instead of adding them here directly.
# See /usr/share/doc/bash-doc/examples in the bash-doc package.

if [ -f ~/.bash_aliases ]; then
    . ~/.bash_aliases
fi

# enable programmable completion features (you don't need to enable
# this, if it's already enabled in /etc/bash.bashrc and /etc/profile
# sources /etc/bash.bashrc).
if ! shopt -oq posix; then
  if [ -f /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion
  elif [ -f /etc/bash_completion ]; then
    . /etc/bash_completion
  fi
fi

# mp4museum, modified 2026 in https://github.com/lotech/mp4museum (see git history)
# only on the screen's console (not over SSH, and only one player: not on tty2 and so on)
if [[ $(tty) == /dev/tty1 ]]; then

# the web interface runs as a service: systemctl status mp4m-webservice

# mp4museum autostart. If the player stops by itself (an error, or out of memory), it is
# started again; stopped on purpose (Ctrl-C) you get the console.
# Its output is in /tmp/mp4museum.log (the web interface shows the end of it).
setterm -cursor off
clear
: > /tmp/mp4museum.log
wait=3
while true; do
  started=$SECONDS
  python3 /boot/mp4museum.py >> /tmp/mp4museum.log 2>&1
  status=$?
  [ $status -eq 0 ] && break
  # Ctrl-C with a player script from before (edited, so kept by install.sh): Python 3.7 exits with 1
  # and a KeyboardInterrupt traceback, later versions with 130. A stop on purpose too.
  if [ $status -eq 130 ] || tail -n 3 /tmp/mp4museum.log | grep -q '^KeyboardInterrupt'; then
    break
  fi
  echo "$(date '+%F %T') the player stopped (exit code $status), starting it again" >> /tmp/mp4museum.log
  # the log is in memory: keep the end of it
  if [ "$(stat -c %s /tmp/mp4museum.log)" -gt 1000000 ]; then
    tail -c 200000 /tmp/mp4museum.log > /tmp/mp4museum.log.tmp && mv /tmp/mp4museum.log.tmp /tmp/mp4museum.log
  fi
  # stopping again straight away (e.g. an error in an edited script): wait longer each time, up to a minute
  if [ $((SECONDS - started)) -lt 60 ]; then
    wait=$((wait * 2 > 60 ? 60 : wait * 2))
  else
    wait=3
  fi
  sleep $wait
done
setterm -cursor on

fi

echo "Welcome to console. Please read the HowTo Hack on mp4museum.org"
