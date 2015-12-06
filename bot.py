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

# channel for publicly announcing when someone is verified
# (This will get populated with the channel config after we connect)
announce_channel = None

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

    if content.startswith('!help'):
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
        elif message.channel.id == config['channel']:
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
    # TODO: add user to correct group
    format_args = dict(
            name = user.name,
            mention_name = user.mention(),
            id = user.id,
            link = link
            )
    priv_message = config['verified_private_message'].format(**format_args)
    client.send_message(user, priv_message)
    if announce_channel is not None:
        pub_message = config['verified_public_message'].format(**format_args)
        client.send_message(announce_channel, pub_message)

    verified_users.add(user.id)
    print('Verified user {} successfully'.format(user.id))

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
    # TODO: maybe make this be a different config
    if config['announce_channel']:
        for channel in client.get_all_channels():
            if channel.id == config['announce_channel']:
                announce_channel = channel
                break

client.run()
