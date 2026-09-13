import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from flask import Flask
from threading import Thread

# ==========================================
# WEB SERVER FOR RENDER (KEEP-ALIVE)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "The Lightning Music Bot is active!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# YTDLP CONFIGURATION (ULTRA FAST)
# ==========================================
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# ==========================================
# GUILD MUSIC STATE & QUEUE MANAGEMENT
# ==========================================
class Song:
    def __init__(self, data, requester):
        self.title = data.get('title', 'Unknown Title')
        # Si es una búsqueda plana, construimos el enlace web directo con el ID para evitar demoras
        vid_id = data.get('id') or data.get('url', '')
        if len(vid_id) == 11 and not vid_id.startswith('http'):
            self.webpage_url = f"https://www.youtube.com/watch?v={vid_id}"
        else:
            self.webpage_url = data.get('webpage_url', vid_id)
            
        self.thumbnail = data.get('thumbnail', None)
        self.duration = data.get('duration', 0)
        self.requester = requester
        
        if self.duration:
            mins, secs = divmod(self.duration, 60)
            hours, mins = divmod(mins, 60)
            self.duration_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
        else:
            self.duration_str = "Desconocida"

class GuildMusicPlayer:
    def __init__(self, ctx):
        self.bot = ctx.bot
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.queue = asyncio.Queue()
        self.current = None
        self.current_message = None

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while True:
            self.current = None
            
            try:
                self.current = await asyncio.wait_for(self.queue.get(), timeout=180.0)
            except asyncio.TimeoutError:
                if self.guild.voice_client:
                    await self.guild.voice_client.disconnect()
                    if self.channel:
                        await self.channel.send("👋 Me salí del canal de voz por inactividad.")
                break

            try:
                loop = self.bot.loop
                # Extracción rápida del flujo de audio real
                data = await loop.run_in_executor(None, lambda: ytdl.extract_info(self.current.webpage_url, download=False))
                
                if 'entries' in data:
                    data = data['entries'][0]

                playback_url = data['url']
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(playback_url, **ffmpeg_options), volume=1.0)
                
                embed = self.build_now_playing_embed()
                view = MusicControlView(self)
                
                if self.current_message:
                    try:
                        await self.current_message.delete()
                    except:
                        pass
                
                self.current_message = await self.channel.send(embed=embed, view=view)

                event = asyncio.Event()
                self.guild.voice_client.play(source, after=lambda e: event.set())
                await event.wait()

            except Exception as e:
                print(f"Error in player loop: {e}")
                if self.channel:
                    await self.channel.send(f"❌ Ocurrió un error al reproducir la canción: {e}")

            if self.queue.empty():
                await asyncio.sleep(1)
                if self.queue.empty() and self.guild.voice_client and not self.guild.voice_client.is_playing():
                    await self.guild.voice_client.disconnect()
                    if self.channel:
                        await self.channel.send("👋 Lista de reproducción finalizada. ¡Hasta pronto!")
                    break

    def build_now_playing_embed(self):
        embed = discord.Embed(
            title="🎶 Reproduciendo ahora",
            description=f"**[{self.current.title}]({self.current.webpage_url})**",
            color=discord.Color.blurple()
        )
        if self.current.thumbnail:
            embed.set_thumbnail(url=self.current.thumbnail)

        embed.add_field(name="Duración", value=self.current.duration_str, inline=True)
        embed.add_field(name="Solicitado por", value=self.current.requester.mention, inline=True)

        if not self.queue.empty():
            next_song = list(self.queue._queue)[0]
            embed.add_field(name="⏭️ Siguiente en la lista", value=f"**{next_song.title}**", inline=False)
        else:
            embed.add_field(name="⏭️ Siguiente en la lista", value="*No hay más canciones en la cola.*", inline=False)

        embed.set_footer(text="Usa los botones de abajo para controlar la reproducción.")
        return embed

players = {}

def get_player(ctx):
    if ctx.guild.id not in players:
        players[ctx.guild.id] = GuildMusicPlayer(ctx)
    return players[ctx.guild.id]

# ==========================================
# INTERACTIVE BUTTONS VIEW
# ==========================================
class MusicControlView(discord.ui.View):
    def __init__(self, player: GuildMusicPlayer):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pausar / Reanudar", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.response.send_message("❌ El bot no está conectado.", ephemeral=True)
            return
        
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Música pausada.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Música reanudada.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No hay música reproduciéndose.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("⏭️ Canción saltada.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ No hay nada reproduciéndose para saltar.", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        player = players.get(interaction.guild.id)
        if player:
            while not player.queue.empty():
                try:
                    player.queue.get_nowait()
                except:
                    break
        if vc:
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message("⏹️ Reproducción detenida y bot desconectado.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ El bot no está en un canal de voz.", ephemeral=True)

# ==========================================
# BOT SETUP & COMMANDS
# ==========================================
class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=".", intents=intents, help_command=None)

    async def setup_hook(self):
        await self.tree.sync()
        print("⚡ Lightning Fast Music Bot initialized.")

bot = MusicBot()

@bot.tree.command(name="play", description="Reproduce música al instante de YouTube y crea cola")
@app_commands.describe(search="Nombre de la canción o link directo de YouTube")
async def play(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ ¡Debes estar en un canal de voz para usar este comando!", ephemeral=True)
        return

    await interaction.response.defer()

    channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if not voice_client:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    try:
        loop = bot.loop
        
        # Extracción ultrarrápida preliminar (solo metadatos ligeros del primer resultado)
        query = search if search.startswith("http") else f"ytsearch1:{search}"
        
        partial_data = await loop.run_in_executor(
            None, 
            lambda: ytdl.extract_info(query, download=False, process=False)
        )
        
        if 'entries' in partial_data:
            song_data = partial_data['entries'][0]
        else:
            song_data = partial_data

        song = Song(song_data, interaction.user)
        player = get_player(interaction)

        await player.queue.put(song)

        if not voice_client.is_playing() and not voice_client.is_paused():
            bot.loop.create_task(player.player_loop())
            await interaction.followup.send(f"⚡ Reproduciendo al instante: **{song.title}**")
        else:
            await interaction.followup.send(f"➕ Añadido a la cola: **{song.title}** (Posición #{player.queue.qsize()})")

    except Exception as e:
        await interaction.followup.send(f"❌ Ocurrió un error al buscar la canción: {e}")


@bot.tree.command(name="leave", description="Saca al bot del canal de voz y limpia la cola")
async def leave(interaction: discord.Interaction):
    player = players.get(interaction.guild.id)
    if player:
        while not player.queue.empty():
            try:
                player.queue.get_nowait()
            except:
                break
    
    voice_client = interaction.guild.voice_client
    if voice_client:
        await voice_client.disconnect()
        await interaction.response.send_message("👋 ¡Me he salido del canal de voz!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ No estoy conectado a ningún canal de voz.", ephemeral=True)

if __name__ == "__main__":
    keep_alive()
    bot.run(os.environ['DISCORD_TOKEN'])
