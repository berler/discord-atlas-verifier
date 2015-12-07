#!/usr/bin/python3

import discord
import json
import requests
from bs4 import BeautifulSoup

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

# channel for publicly announcing when someone is verified
# (This will get populated with the channel config after we connect)
announce_channel = None
mod_channel = None

client = discord.Client()
client.login(config['email'], config['password'])

@client.event
def on_member_join(member):
    server = member.server
    if server.id != config['server']:
        return

    # debug!
    print('{} [id = {}] joined the server'.format(member.name, member.id))

    help_message(member)

@client.event
def on_message(message):
    if ignore_message(message):
        return

    user = message.author
    content = message.content

    if content.startswith('!help') or content.startswith('!hello'):
        help_message(user)
    elif not is_verified(user):
        try_verify(message)

def ignore_message(message):
    if message.author == client.user:
        # ignore messages from ourselves!
        return True

    # TODO: add per-user rate limiting

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

    # otherwise, ignore message
    return True

def help_message(user):
    m = config['help_message'].format(
            name = user.name,
            mention_name = user.mention(),
            id = user.id,
            )
    client.send_message(user, m)

def is_verified(user):
    # TODO: we might want some fallback to query the server in case our
    # local verified_users cache isn't right.
    return user.id in verified_users

def try_verify(message):
    user = message.author
    content = message.content
    for word in content.split():
        if word.lower().startswith(config['verify_url_prefix']):
            return verify(user, word)
        elif word.startswith('https://') or word.startswith('http://'):
            client.send_message(user, config['invalid_link_message'])
            return

def verify(user, link):
    print('Attempting to verify user {} with link {}'.format(user.id, link))

    # TODO: we might want a better way than just stuffing hard-coded cookies
    # (like auto login with user and pass and get the cookies from that)
    r = requests.get(link, cookies=config['verify_cookies'])
    if r.status_code != requests.codes.ok:
        print('Error loading verification page:', r.status_code)
        client.send_message(user, config['verification_error'])
        return

    # note: apparently the 'lxml' parser is faster, but you need to install it
    soup = BeautifulSoup(r.content, 'html.parser')
    posts = soup.findAll('div', {'class': 'ItemContent Activity'})
    for post in posts:
        # TODO: verify that this post is by the correct author
        text = ' '.join(post.findAll(text=True))
        print('Found Post:', text)
        if user.id in text and 'discord' in text.lower():
            # verify success!
            return verify_success(user, link)

    print('No verification post found for user', user.id)
    msg = config['missing_verification_post'].format(
            id = user.id,
            )
    client.send_message(user, msg)

def verify_success(user, link):
    forum_id = get_forum_id(link)
    format_args = dict(
            name = user.name,
            mention_name = user.mention(),
            id = user.id,
            link = link,
            forum_id = forum_id
            )

    # First, some sanity checks. If there are multiple Discord users with
    # the same name, or the forum account has been used before, we will alert
    # the mods, and not verify the user. We want to avoid having impersonators.
    if dupe_user_names(user):
        client.send_message(user, config['verified_profile_duplicate_name'])
        if mod_channel is not None and user.id not in manually_verified_users:
            msg = config['verified_public_message'].format(**format_args)
            msg += config['verified_profile_duplicate_name_mods'].format(
                    name = user.name,
                    )
            client.send_message(mod_channel, msg)
            manually_verified_users.add(user.id)
        return
    elif forum_account_used(forum_id):
        client.send_message(user, config['verified_profile_before'])
        if mod_channel is not None and user.id not in manually_verified_users:
            msg = config['verified_public_message'].format(**format_args)
            msg += config['verified_profile_before_mods']
            client.send_message(mod_channel, msg)
            manually_verified_users.add(user.id)
        return

    # add user roles
    # we need to first find the correct Member object on the server
    # (we can't modify roles on User objects directly)
    member = get_member(user)
    if member is None:
        # TODO: make a proper error message, this shouldn't happen
        return
    client.add_roles(member, discord.Role(id=config['verified_role']))

    verified_forum_ids.add(forum_id)
    with open('verified_forum_ids.txt', 'a+') as f:
        f.write(forum_id + '\n')

    priv_message = config['verified_private_message'].format(**format_args)
    client.send_message(user, priv_message)
    if announce_channel is not None:
        pub_message = config['verified_public_message'].format(**format_args)
        client.send_message(announce_channel, pub_message)

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
    for server in client.servers:
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
    for server in client.servers:
        if server.id != config['server']:
            continue

        for member in server.members:
            if member.id == user.id:
                return member

    # member not found
    return None

@client.event
def on_ready():
    print('Logged in as')
    print(client.user.name)
    print(client.user.id)
    print('------')

    # get a list of all the users already verified so we can ignore them
    for server in client.servers:
        if server.id != config['server']:
            continue

        for member in server.members:
            for role in member.roles:
                if role.id == config['verified_role']:
                    verified_users.add(member.id)

    print('already verified users:', verified_users)
    print('------')

    # get the channel we want to use for announcements
    if config['announce_channel']:
        for channel in client.get_all_channels():
            if config['announce_channel'] in [channel.id, channel.name]:
                # this is ugly, but we need to tell python we are setting
                # the global var
                global announce_channel
                announce_channel = channel
            if config['mod_channel'] in [channel.id, channel.name]:
                global mod_channel
                mod_channel = channel

client.run()
