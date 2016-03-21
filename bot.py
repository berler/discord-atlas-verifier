#!/usr/bin/python3

import asyncio
import datetime
import logging
import subprocess
import sys
import json

import aiohttp
import discord
from discord.ext import commands
import cupboard
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.WARNING)

startup_time = datetime.datetime.today()

def get_version():
    try:
        args = ['git', 'describe', '--tags', '--always']
        return subprocess.check_output(args).decode('utf-8', 'ignore').strip()
    except Exception:
        return 'unknown'

version = get_version()

config = {}
with open('config.json', 'r') as f:
    config.update(json.load(f))

# keep a set of verified user id's so we can ignore them
verified_users = set()
verified_forum_ids = set()
# keep track of users we are to verify manually (to avoid spamming mod chan)
manually_verified_users = set()

# hack to create file if it doesn't exist yet
with open('verified_forum_ids.txt', 'a+') as f:
    pass
with open('verified_forum_ids.txt', 'r') as f:
    for line in f.readlines():
        verified_forum_ids.add(line.strip())

# The channels we care about. public_channel (also known as the requests
# channel) is where the bot will send public welcome messages as well as
# announcing when people are verified. mod_channel is where the bot will
# send messages that should only be visible to mods.
public_channel = None
mod_channel = None

bot = commands.Bot(command_prefix='!', pm_help=True, help_attrs={'name': 'cmdhelp'})

@asyncio.coroutine
def welcome(user):
    m = config['welcome_message'].format(
            name = user.name,
            mention_name = user.mention,
            id = user.id,
            )
    if public_channel is not None:
        yield from bot.send_message(public_channel, m)

@asyncio.coroutine
def help_message(user):
    m = config['help_message'].format(
            name = user.name,
            mention_name = user.mention,
            id = user.id,
            )
    yield from bot.send_message(user, m)

@bot.listen
@asyncio.coroutine
def on_member_join(member):
    server = member.server
    if server.id != config['server']:
        return

    # debug!
    print('{} [id = {}] joined the server'.format(member.name, member.id))

    yield from help_message(member)
    yield from welcome(member)

@bot.command(pass_context=True, aliases=['hello'])
@asyncio.coroutine
def help(ctx):
    """Shows instructions on how to register your account"""
    yield from help_message(ctx.message.author)

@bot.listen('on_message')
@asyncio.coroutine
def try_verify_user(message):
    if ignore_message(message):
        return

    user = message.author
    content = message.content

    if is_verified(user):
        return

    for word in content.split():
        if word.lower().startswith(config['verify_url_prefix']):
            ret = yield from verify_provided_link(user, word)
            return ret
        elif word.startswith('https://') or word.startswith('http://'):
            yield from bot.send_message(user, config['invalid_link_message'])
            return

def ignore_message(message):
    if message.author == bot.user:
        # ignore messages from ourselves!
        return True

    # TODO: add per-user rate limiting?

    if message.server is None:
        # private message. never ignore
        return False
    elif message.server.id == config['server']:
        if config['channel'] == '*':
            # all channels are okay
            return False
        elif config['channel'] in [message.channel.id, message.channel.name]:
            # message was in correct channel
            return False
        elif config['mod_channel'] in [message.channel.id, message.channel.name]:
            # allow mod channel too
            return False

    # otherwise, ignore message
    return True

def is_verified(user):
    # TODO: we might want some fallback to query the server in case our
    # local verified_users cache isn't right.
    return user.id in verified_users

def is_mod(user):
    # always check user roles for admin
    member = get_member(user)
    if member is None:
        # not even on server, so can't be a mod
        return False

    for role in member.roles:
        if config['mod_role'] == role.id:
            return True

    return False

def mod_only_command():
    return commands.check(lambda ctx: is_mod(ctx.message.author))

@bot.command()
@mod_only_command()
@asyncio.coroutine
def about():
    """Show version info for the bot"""
    msg = 'python version: {}\ndiscord.py version: {}\nbot version: {}'.format(
            sys.version.split()[0], discord.__version__, version)
    yield from bot.say(msg)

@bot.command()
@mod_only_command()
@asyncio.coroutine
def stats():
    """Show bot stats"""
    uptime = datetime.datetime.today() - startup_time
    # strip ugly microseconds
    nice_uptime = datetime.timedelta(uptime.days, uptime.seconds, 0)
    msg = 'Bot uptime: {}\nVerified users: {}\nUnique forum profiles verified: {}'.format(
            nice_uptime, len(verified_users), len(verified_forum_ids))
    yield from bot.say(msg)

@bot.command()
@mod_only_command()
@asyncio.coroutine
def refresh():
    """Refresh the verified users cache"""
    # refresh verified users cache
    new_verified_users = set()
    for server in bot.servers:
        if config['server'] != server.id:
            continue

        for member in server.members:
            if config['verified_role'] in [role.id for role in member.roles]:
                new_verified_users.add(member.id)

    added = len(new_verified_users.difference(verified_users))
    removed = len(verified_users.difference(new_verified_users))

    if added == 0 and removed == 0:
        msg = 'Verified cache refreshed (no changes)'
        yield from bot.say(msg)
        return

    # replace
    verified_users.clear()
    verified_users.update(new_verified_users)

    msg = 'Verified cache refreshed ({} added, {} removed)'.format(added, removed)
    yield from bot.say(msg)

@asyncio.coroutine
def verify_provided_link(user, link):
    print('Attempting to verify user {} with link {}'.format(user.id, link))

    # TODO: we might want a better way than just stuffing hard-coded cookies
    # (like auto login with user and pass and get the cookies from that)
    with aiohttp.ClientSession(cookies=config['verify_cookies']) as session:
        r = yield from session.get(link)
        if r.status != 200:
            print('Error loading verification page:', r.status_code)
            yield from bot.send_message(user, config['verification_error'])
            return

        # note: apparently the 'lxml' parser is faster, but you need to install it
        content = yield from r.text()
        soup = BeautifulSoup(content, 'html.parser')
        posts = soup.findAll('div', {'class': 'ItemContent Activity'})
        for post in posts:
            # TODO: verify that this post is by the correct author
            text = ' '.join(post.findAll(text=True))
            print('Found Post:', text)
            if user.id in text and 'discord' in text.lower():
                # verify success!
                ret = yield from verify_success(user, link)
                return ret

    print('No verification post found for user', user.id)
    msg = config['missing_verification_post'].format(
            id = user.id,
            )
    yield from bot.send_message(user, msg)

@asyncio.coroutine
def verify_success(user, link):
    forum_id = get_forum_id(link)
    format_args = dict(
            name = user.name,
            mention_name = user.mention,
            id = user.id,
            link = link,
            forum_id = forum_id
            )

    # First, some sanity checks. If there are multiple Discord users with
    # the same name, or the forum account has been used before, we will alert
    # the mods, and not verify the user. We want to avoid having impersonators.
    if dupe_user_names(user):
        yield from bot.send_message(user, config['verified_profile_duplicate_name'])
        if mod_channel is not None and user.id not in manually_verified_users:
            msg = config['verified_public_message'].format(**format_args)
            msg += config['verified_profile_duplicate_name_mods'].format(
                    name = user.name,
                    )
            yield from bot.send_message(mod_channel, msg)
            manually_verified_users.add(user.id)
        return
    elif forum_account_used(forum_id):
        yield from bot.send_message(user, config['verified_profile_before'])
        if mod_channel is not None and user.id not in manually_verified_users:
            msg = config['verified_public_message'].format(**format_args)
            msg += config['verified_profile_before_mods']
            yield from bot.send_message(mod_channel, msg)
            manually_verified_users.add(user.id)
        return

    # add user roles
    # we need to first find the correct Member object on the server
    # (we can't modify roles on User objects directly)
    member = get_member(user)
    if member is None:
        # TODO: make a proper error message, this shouldn't happen
        return
    yield from bot.add_roles(member, discord.Role(id=config['verified_role']))

    verified_forum_ids.add(forum_id)
    with open('verified_forum_ids.txt', 'a+') as f:
        f.write(forum_id + '\n')

    priv_message = config['verified_private_message'].format(**format_args)
    yield from bot.send_message(user, priv_message)
    if public_channel is not None:
        pub_message = config['verified_public_message'].format(**format_args)
        yield from bot.send_message(public_channel, pub_message)

    verified_users.add(user.id)
    print('Verified user {} successfully'.format(user.id))

def get_forum_id(link):
    # strip the url prefix
    url_suffix = link[len(config['verify_url_prefix']):]
    for url_part in url_suffix.split('/'):
        if url_part.isdigit():
            return url_part

def dupe_user_names(user):
    count = 0
    for server in bot.servers:
        if server.id != config['server']:
            continue

        for member in server.members:
            # TODO: do a similarity check instead of comparing lowercase
            # The point is to check for impersonators, so we might want to
            # check for variations of the name like 'I' -> 'l' etc
            if member.name.lower() == user.name.lower():
                count += 1

    return count > 1

def forum_account_used(forum_id):
    return forum_id in verified_forum_ids

def get_member(user):
    if isinstance(user, discord.Member) and user.server.id == config['server']:
        return user

    for server in bot.servers:
        if server.id != config['server']:
            continue
        return server.get_member(user.id)

    # member not found
    return None

@bot.event
@asyncio.coroutine
def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')

    # get a list of all the users already verified so we can ignore them
    for server in bot.servers:
        if server.id != config['server']:
            continue

        print('total users on the server:', len(server.members))

        for member in server.members:
            for role in member.roles:
                if role.id == config['verified_role']:
                    verified_users.add(member.id)

    print('already verified users:', len(verified_users))
    print('------')

    # find the channels we care about
    for channel in bot.get_all_channels():
        if config['channel'] in [channel.id, channel.name]:
            # this is ugly, but we need to tell python we are setting
            # the global var
            global public_channel
            public_channel = channel
        if config['mod_channel'] in [channel.id, channel.name]:
            global mod_channel
            mod_channel = channel

bot.run(config['email'], config['password'])
