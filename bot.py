import asyncio
import aiohttp
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import ssl
import ffmpeg

# Create a custom aiohttp ClientSession
async def get_session():
    return aiohttp.ClientSession()

intents = discord.Intents.default()
intents.message_content = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# yt-dlp options
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicPlayer:
    def __init__(self, ctx):
        self.bot = ctx.bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Use asyncio.wait_for instead of asyncio.timeout
                source = await asyncio.wait_for(self.queue.get(), timeout=300)
            except asyncio.TimeoutError:
                return self.destroy(self.guild)

            if not isinstance(source, YTDLSource):
                try:
                    source = await YTDLSource.from_url(source, loop=self.bot.loop, stream=True)
                except Exception as e:
                    await self.channel.send(f'There was an error processing your song.\n'
                                            f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self.guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self.channel.send(f'**Now Playing:** `{source.title}`')
            await self.next.wait()

            source.cleanup()
            self.current = None

            try:
                await self.np.delete()
            except discord.HTTPException:
                pass

    def destroy(self, guild):
        return self.bot.loop.create_task(self.cog.cleanup(guild))

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command(name='play', help='To play song')
    async def play(self, ctx, *, url):
        if not ctx.message.author.voice:
            await ctx.send("{} is not connected to a voice channel".format(ctx.message.author.name))
            return

        if ctx.voice_client is None:
            await ctx.author.voice.channel.connect()

        player = self.get_player(ctx)

        async with ctx.typing():
            try:
                source = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            except Exception as e:
                await ctx.send(f'An error occurred: {str(e)}')
            else:
                await player.queue.put(source)
                await ctx.send(f'**Added to queue:** {source.title}')

    @commands.command(name='leave', help='To make the bot leave the voice channel')
    async def leave(self, ctx):
        await self.cleanup(ctx.guild)

    @commands.command(name='loop', help='Loop current song')
    async def loop(self, ctx):
        player = self.get_player(ctx)
        if player.current:
            await player.queue.put(player.current.url)
            await ctx.send(f'Looping current song: {player.current.title}')
        else:
            await ctx.send('No song is currently playing.')

    @commands.command(name='queue', help='Show the music queue')
    async def queue_info(self, ctx):
        player = self.get_player(ctx)
        if player.queue.empty():
            await ctx.send('Queue is empty.')
        else:
            upcoming = list(player.queue._queue)
            fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
            embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
            await ctx.send(embed=embed)

    @commands.command(name='skip', help='Skip current song')
    async def skip(self, ctx):
        if ctx.voice_client is None:
            return await ctx.send("Not connected to a voice channel.")

        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send('Skipped the current song.')
        else:
            await ctx.send('Not playing any music right now.')

    @commands.command(name='stop', help='Stop playing music')
    async def stop(self, ctx):
        player = self.get_player(ctx)
        player.queue._queue.clear()
        if ctx.voice_client:
            ctx.voice_client.stop()
        await ctx.send('Stopped playing music and cleared the queue.')

async def setup(bot):
    await bot.add_cog(Music(bot))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await setup(bot)

# Set up the custom session
@bot.event
async def on_connect():
    bot.http.session = await get_session()

bot.run('')
