import time
import telebot
import os
import threading
from flask import Flask
from telebot import types
from telebot.types import BotCommand
from telebot.types import InlineQueryResultArticle, InputTextMessageContent 
from youtube_search import YoutubeSearch
user_states={}

BOT_TOKEN = os.environ["BOT_TOKEN"]
bot=telebot.TeleBot(BOT_TOKEN)

srt=r'''⭐ *Welcome to* 𝐀𝐋𝐎𝐍𝐄 𝐌𝐎𝐍𝐒𝐓𝐄𝐑 𝐂𝐎𝐃𝐈𝐍𝐆 🙇

Here, you can request help or ask any questions about programming.
All answers are generated using *30+ top-level AI models* to ensure maximum accuracy.

Want to learn coding? You can master any programming language directly from our website!

🔗 Type _/web_ to get full details and access.'''
menu = '''•/start - _Starts or restarts the bot and displays the welcome message_.\n
​•/help - _Report, Ask for Support and send your Feedback_.\n
​•/menu - _Displays the list of all available commands in the bot._ \n
​•/web - _Helps you open our programming website_.\n
•/more - _Get all function details which our bot supports_'''

more=r'''Apart from the standard Commands, this bot also supports normal text commands\. You can simply send specific text keywords to trigger instant replies\! To explore them, just check our guide below\:

>•Type '/more' for all extra commands our bot supports with inline button

Send or type \'`/more`\' to find them'''

help='''*Welcome to 𝐀𝐥𝐨𝐧𝐞 𝐌𝐨𝐧𝐬𝐭𝐞𝐫  𝐒𝐮𝐩𝐩𝐨𝐫𝐭:*

Are you facing any problems, running into bugs, or need help with your code? Connect through our official resources below to get the support you need.

🌐 *𝐈𝐦𝐩𝐨𝐫𝐭𝐚𝐧𝐭 𝐋𝐢𝐧𝐤𝐬:*
• 📢 *Telegram Channel:* @AloneMonsterCoding
• 🤖 *Our Telegram Bot:* @AloneMonster\\_Robot
• 🎥 *YouTube Channel:* https://youtube.com/@AloneMonsterCoding
• 💻 *GitHub Profile:* https://github.com/alone-monster
• 🌍 *Official Website:* https://alone-monster.github.io

💡 *Feedback & Suggestions:* If you want to report a bug or share a suggestion, drop a message directly to the admin account. Keep coding!'''


menu_btn = types.InlineKeyboardButton(text="Menu", callback_data="menubtn")

help_btn = types.InlineKeyboardButton(text="Help", callback_data="helpbtn")

start_btn = types.InlineKeyboardButton(text="Start", callback_data="startbtn")

more_btn=types.InlineKeyboardButton(text='More', callback_data='morebtn')


inline_start_button = types.InlineKeyboardMarkup()
inline_start_button.add(menu_btn, help_btn, more_btn)
@bot.message_handler(commands=["start"])
def send_welcome(message):
        
        name=message.from_user.first_name
        id = message.from_user.id
        start_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        downloader_key = types.KeyboardButton("Video Downloader")
        start_keyboard.add(downloader_key)
        welcome_text = f"[{name}](tg://user?id={id}),\n {srt}"
        bot.reply_to(message,welcome_text, parse_mode="markdown", reply_markup=inline_start_button)
        bot.send_message(
        chat_id = message.chat.id,
        text = 'Choose keyboard Options If you need👇',
        parse_mode = "HTML",
        reply_markup = start_keyboard
         )
         





inline_menu_button = types.InlineKeyboardMarkup()
inline_menu_button.add(start_btn, help_btn, more_btn)

@bot.callback_query_handler(func =lambda call: call.data == 'menubtn')
def menu_callback(call):
    
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text= f"{menu}", reply_markup=inline_menu_button,
        parse_mode="Markdown")
 
@bot.callback_query_handler(func=lambda call: call.data == 'startbtn')
def start_callback(call):
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=srt, reply_markup=inline_start_button,
        parse_mode="Markdown"
    )

inline_help_button= types.InlineKeyboardMarkup()
inline_help_button.add(start_btn, menu_btn, more_btn)
@bot.callback_query_handler(func=lambda call: call.data =='helpbtn')
def help_callback(call):
     name=call.from_user.first_name
     id = call.from_user.id
     bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f'👋 *Hello* [{name}](tg://user?id={id})
        ,{help}',
        parse_mode="Markdown",
        reply_markup=inline_help_button
     )
     
@bot.callback_query_handler(func=lambda call: call.data=='morebtn')
def morebtn(call):
     inline_more_button = types.InlineKeyboardMarkup()
     inline_more_button.add(start_btn, menu_btn, help_btn)
     bot.edit_message_text(
       chat_id=call.message.chat.id,
       message_id=call.message.message_id,
       text=more,
       parse_mode='MarkdownV2',
       reply_markup= inline_more_button
     )
     
commands=[BotCommand("/start", "Start AI Code Guide Bot"),
BotCommand("/help", "Report & ask for Support or Feedback"),
BotCommand("/menu", "Get all detailed function list"),
BotCommand("/web", "Get full access of Website"),
BotCommand("/more", "Get details of more features")]
 
bot.set_my_commands(commands)


web = """If you are interested in technical content, programming, or web development, we invite you to explore our official platform.

✨ 𝐊𝐞𝐲 𝐅𝐞𝐚𝐭𝐮𝐫𝐞𝐬 𝐀𝐯𝐚𝐢𝐥𝐚𝐛𝐥𝐞 𝐎𝐧 𝐎𝐮𝐫 𝐖𝐞𝐛𝐬𝐢𝐭𝐞:
• *Comprehensive Courses*: Learn Python, HTML/CSS, JavaScript, C++, and more fully free.
• *Mobile Development*: Learn specialized tips and tricks for coding on mobile devices.
• *Code Snippets*: Access ready-to-use, reusable code blocks for your projects.
• *Terminal Guides*: Master command line environments, Termux, and CMD tools.
• *Tech Tutorials*: Watch step-by-step troubleshooting and technical trick videos.

⭐ *100% Free Access* — Start learning without any barriers!

🔗*Official Website*: _https://alone-monster.github.io_ `[Google Certified]`"""

inline_web_button = types.InlineKeyboardMarkup()
vweb_btn = types.InlineKeyboardButton(text='Visit Website', url="https://alone-monster.github.io/")
syt_btn = types.InlineKeyboardButton(text='Subscribe YouTube', url='https://youtube.com/@alonemonstercoding')
fgithub_btn = types.InlineKeyboardButton(text='Follow on GitHub', url='https://github.com/alone-monster/')
inline_web_button.add(vweb_btn)
inline_web_button.add(syt_btn)
inline_web_button.add(fgithub_btn)
@bot.message_handler(commands=["web"])
def web_text(message):
    username= message.from_user.username
    username_text= f" 👋 *Hello @{username}*"
    bot.reply_to(message, f"{username_text}, {web}", parse_mode ="Markdown", reply_markup= inline_web_button)


bug_btn = types.InlineKeyboardButton(text="Bugs", callback_data="bugbtn")
suggestion_btn = types.InlineKeyboardButton(text="Suggestion", callback_data="suggestionbtn")

@bot.message_handler(func= lambda message: message.text is not None and message.text.lower() == 'feedback') 
def report_reply(message):
    inline_feedback_button = types.InlineKeyboardMarkup()
    inline_feedback_button.add(bug_btn, suggestion_btn)
    
    bot.reply_to(message, "choose your feedback type:",reply_markup=inline_feedback_button)
    
    
@bot.callback_query_handler(func=lambda call: call.data=='bugbtn')
def bug_call(call):
    user_states[call.message.chat.id]='waiting_for_bug details'
    
    feedback_keyboard=types.ReplyKeyboardMarkup(resize_keyboard=True)
    cancel_key=types.KeyboardButton('❌Cancel❌')
    feedback_keyboard.add(cancel_key)
    bot.edit_message_text(
      chat_id=call.message.chat.id,
      message_id= call.message.message_id,
      text='Well! Type what is the Bugs details you\'re facing:'
      )
    bot.send_message(
      chat_id=call.message.chat.id,
      text="To cancel the request, Send `❌Cancel❌`",
      parse_mode='Markdown',
      reply_markup=feedback_keyboard)

@bot.message_handler(func=lambda message: user_states.get(message.chat.id) == 'waiting_for_bug details')
def capture_bug_details(message):
      if message.text == '❌Cancel❌':
          bot.reply_to(message, "Feedback Request Cancelled", reply_markup= types.ReplyKeyboardRemove())
          user_states.pop(message.chat.id, None)
          return
          
      username = message.from_user.username
      id = message.from_user.id
      first_n = message.from_user.first_name
      last_n = message.from_user.last_name
      Name = f"{first_n} {last_n}"
      text_message=f'''ID: {id} \nName: <a href='tg://user?id={id}'>{Name}</a> \nUsername: @{username} \nBug Report: {message.text}'''
      
      bot.send_message(
           5363583219,
           text=text_message,
           parse_mode='HTML')
           
           
      bot.reply_to(
           message, "Your Feedback has been sent succesfully", reply_markup=types.ReplyKeyboardRemove()
      )
      user_states.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call:call.data=='suggestionbtn')
def suggestion(call):
    user_states[call.message.chat.id]='user_suggestion'
    feedback_keyboard=types.ReplyKeyboardMarkup(resize_keyboard=True)
    cancel_key=types.KeyboardButton('❌Cancel❌')
    feedback_keyboard.add(cancel_key)
    bot.edit_message_text(
      chat_id=call.message.chat.id,
      message_id=call.message.message_id,
      text="Well! Type your Suggestion about the Bot and our Website...!!",
    )
    bot.send_message(
        chat_id=call.message.chat.id,
        text='To cancel the request, Send `❌Cancel❌`',
        parse_mode='Markdown',
        reply_markup=feedback_keyboard
    )
    
@bot.message_handler(func=lambda message: user_states.get(message.chat.id)=='user_suggestion')
def suggestion_ask(message):
    if message.text == '❌Cancel❌':
        bot.reply_to(message, 'Feedback Request Cancelled', reply_markup=types.ReplyKeyboardRemove())
        user_states.pop(message.chat.id, None)
        return
     
    bot.reply_to(message, 'Your Feedback has been sent successfully', reply_markup=types.ReplyKeyboardRemove())
    
    id=message.from_user.id
    name= f'{message.from_user.first_name} {message.from_user.last_name}'
    username=message.from_user.username
    text_message=f'''ID: {id}\nName: <a href='tg://user?id={id}'>{name}</a>\nUsername: @{username}\nBug Report:{message.text}'''
    bot.send_message(
       5363583219,
       text=text_message,
       parse_mode='HTML'
    )
     
inline_quick_feedback=types.InlineKeyboardMarkup()
feedback_btn=types.InlineKeyboardButton(text='Send Feedback to Admin', callback_data='feedbackbtn')
inline_quick_feedback.add(feedback_btn)
@bot.message_handler(commands=["help"])
def help_text(message):
        bot.reply_to(message,
        f"Welcome @{message.from_user.username}, {help}",
        parse_mode="Markdown",
        reply_markup=inline_quick_feedback)

@bot.callback_query_handler(func=lambda call: call.data=='feedbackbtn')
def send_feedback(call):
    inline_feedback_button = types.InlineKeyboardMarkup()
    inline_feedback_button.add(bug_btn, suggestion_btn)
    
    bot.send_message(
       chat_id=call.message.chat.id,
       text=f"@{call.message.from_user.username},\nChoose your feedback type:",
       reply_markup=inline_feedback_button)
       
menu2='''•/start - <i>Starts or restarts the bot and displays the welcome message</i>.\n
​•/help - <i>Report, Ask for Support and send your Feedback</i>.\n
​•/menu - <i>Displays the list of all available commands in the bot.</i> \n
​•/web - <i>Helps you open our programming website</i>.\n
•/more - <i>Get all function details which our bot supports</i>.
'''
more2=r'<blockquote>•Type `/more` for all extra commands our bot supports with inline button</blockquote>'
@bot.message_handler(commands=["menu"])
def menu_text(message):
    quick_more_button=types.InlineKeyboardButton(text='More', callback_data='morebutton')
    inline_quick_more=types.InlineKeyboardMarkup()
    inline_quick_more.add(quick_more_button)
    username=message.from_user.username
    bot.reply_to(
       message,
       f"@{username}, <b>Menu</b> List is below:\n\n{menu2} {more2}",
       parse_mode='HTML',
       reply_markup=inline_quick_more
    )

more_des=r'''<b>🔹Our Extra Text Command:</b>

•<code><u>Feedback</u></code> - <i>Send your Feedback to admin about the user experience of Bot and Website.</i>
'''
feedback_button=types.InlineKeyboardButton(text='Feedback', callback_data='feedbackbutton')
@bot.message_handler(commands=["more"])
def more_text(message):
    inline_more_box=types.InlineKeyboardMarkup()
    inline_more_box.add(feedback_button)
    bot.reply_to(
       message,
       more_des,
       parse_mode='HTML',
       reply_markup=inline_more_box
    )
@bot.callback_query_handler(func=lambda call: call.data=='feedbackbutton')
def send_feedback(call):
    inline_feedback_button = types.InlineKeyboardMarkup()
    inline_feedback_button.add(bug_btn, suggestion_btn)
    
    bot.send_message(
       chat_id=call.message.chat.id,
       text=f"@{call.message.from_user.username},\nChoose your feedback type:",
       reply_markup=inline_feedback_button)

@bot.callback_query_handler(func=lambda call: call.data=='morebutton')
def send_more_text(call):
    inline_more_box=types.InlineKeyboardMarkup()
    inline_more_box.add(feedback_button)
    bot.send_message(
       chat_id=call.message.chat.id,
       text=more_des,
       parse_mode='HTML',
       reply_markup=inline_more_box
    )
    
@bot.inline_handler(func=lambda inline_data: inline_data.query.lower().startswith('yt'))
def youtube_search(inline_data):
    user_data = inline_data.query.replace("yt", '',1)
    articles = []
    results = YoutubeSearch(
       user_data,
       max_results = 30
    ).to_dict()

    for video in results:
        video_id = video['id']
        title = video['title']
        
        # Thumbnail check karne ka bilkul safe tarika
        thumb_list = video.get('thumbnails', [])
        """
        thumb_list = video['thumbnails'']
        thumb_list = video[]
        """
        thumbnail_url = thumb_list[0] if thumb_list else ""
        
        description = video.get('description', '')
        
        articles.append(InlineQueryResultArticle(
           id = video_id,
           title = title,
           input_message_content = InputTextMessageContent(
              message_text = f"https://www.youtube.com/watch?v={video_id}"
           ),
           description = description,
           thumbnail_url = thumbnail_url
        ))
        
    try:
        bot.answer_inline_query(inline_data.id, articles)
    except telebot.apihelper.ApiTelegramException as e:
        if "query is too old" in str(e):
            print("Query expired, skipping to next search!")
        else:
            raise e

@bot.message_handler(func=lambda message: message.text is not None and message.text=="Video Downloader")
def download(message):
    bot.reply_to(message, "This feature is currently not available !!",
    reply_markup=types.ReplyKeyboardRemove())
















app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_bot():
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)