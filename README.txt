MarkScout - Setup & Run Guide
==================================

WHAT THIS IS
------------
MarkScout is a preliminary, automated trademark-risk screening tool.
It is NOT legal advice and NOT a replacement for a professional trademark
clearance search or a licensed attorney. It checks a brand name against:
  1. A best-effort live USPTO lookup (US)
  2. A best-effort live IP India portal check (India)
  3. A small, hand-maintained internal reference list of well-known marks
     and commonly-descriptive terms

Because the official USPTO and IP India search systems are not designed
for free, unauthenticated, scripted queries, the live lookups will often
be unavailable — the app tells you clearly when that happens instead of
pretending the search was complete. Always double check anything
important with a real trademark search or attorney before you commit
money to a name.


STEP 1 — INSTALL PYTHON
------------------------
1. Go to https://www.python.org/downloads/
2. Download and install Python 3.10 or newer.
   - On Windows: during install, CHECK the box "Add Python to PATH"
     before clicking Install.
   - On Mac: the installer handles this automatically.
3. Confirm it worked. Open:
   - Windows: Command Prompt (search "cmd" in the Start menu)
   - Mac: Terminal (search "Terminal" in Spotlight)
   Type:
       python --version
   You should see something like "Python 3.11.5". If you get an error,
   restart your computer and try again.


STEP 2 — GET THE FILES ONTO YOUR COMPUTER
-------------------------------------------
1. Create a new folder anywhere you like, e.g. "MarkScout" on your
   Desktop.
2. Inside that folder, create a subfolder named exactly "templates".
3. Save the files you were given like this:
       MarkScout/
         app.py
         requirements.txt
         README.txt
         templates/
           index.html
   (index.html MUST be inside the "templates" folder — Flask looks for
   it there specifically.)


STEP 3 — INSTALL THE REQUIRED LIBRARIES
------------------------------------------
1. Open Command Prompt (Windows) or Terminal (Mac).
2. Navigate into your project folder. For example, if you put it on your
   Desktop:
       Windows:  cd Desktop\MarkScout
       Mac:      cd Desktop/MarkScout
3. Install the dependencies:
       pip install -r requirements.txt
   (If "pip" is not recognized on Windows, try "pip3" or
   "python -m pip install -r requirements.txt" instead.)


STEP 4 — RUN THE APP
----------------------
Still inside the project folder, run:
       python app.py
   (or "python3 app.py" on Mac)

You should see output like:
       * Running on http://0.0.0.0:5000

Now open your web browser and go to:
       http://localhost:5000

You should see the MarkScout page. Try searching a brand name!

To stop the app, go back to the Command Prompt / Terminal window and
press Ctrl + C.


STEP 5 — BEFORE YOU TAKE THIS LIVE (IMPORTANT)
-------------------------------------------------
This code is a working starting point, not a finished commercial
product. Before you put it in front of real customers or take real
money:

1. AFFILIATE LINKS
   Open app.py and find AFFILIATE_IDS near the top. Replace the
   placeholder text with your real affiliate IDs from Namecheap,
   GoDaddy, and Hostinger's affiliate programs. Sign up for those
   programs first — they are free to join.

   Do the same for REFERRAL_IDS just below it — these power the
   "Get real legal help" links shown when a name comes back RED or
   YELLOW (Vakilsearch, LegalRaasta, IndiaFilings for India;
   Trademarkia, LegalZoom for the US). Sign up for each service's
   affiliate/referral program and swap in your real IDs.

   1B. SET A REAL SECRET_KEY
   The free-search counter and "unlocked" status live in a signed
   session cookie, which requires a SECRET_KEY. app.py falls back to a
   placeholder key for local testing, but before you deploy, set a real
   one as an environment variable:
       SECRET_KEY=<a long random string>
   On Render: Dashboard → your service → Environment → Add Environment
   Variable. Generate one locally with:
       python -c "import secrets; print(secrets.token_hex(32))"

   1C. CAPTURED EMAILS
   When someone unlocks past the free-search limit, their email is
   appended to data/emails.csv on the server. Two things to know:
     - Free hosting tiers (including Render's free tier) often do NOT
       keep files you write at runtime — the filesystem can reset on
       every deploy or restart. Treat the CSV as a local/dev
       convenience, not your real mailing list.
     - For production, open app.py, find the function
       _save_captured_email(), and add a call to a real email service
       provider's API (Mailchimp, ConvertKit, Brevo, etc.) so captured
       emails actually land somewhere durable — and add double opt-in
       before you send marketing email to them, to stay compliant with
       anti-spam law in your country.
   The free-search limit itself is set by FREE_SEARCH_LIMIT near the
   top of app.py (default: 3) — change the number to adjust it.

2. PREMIUM PAYMENTS
   The "Unlock for $9" button in templates/index.html currently shows a
   demo confirmation box and does NOT charge anyone real money. Before
   you launch:
     - Create a Stripe (stripe.com) or PayPal developer account.
     - Add their official Checkout integration to collect payment.
     - Only call the /api/premium-names endpoint in app.py AFTER your
       server has verified the payment succeeded (e.g. by checking a
       Stripe webhook event). Never reveal the paid content just
       because someone clicked a button — always verify payment first.
   This matters both for your revenue and for trust: if you promise a
   paid feature, it must be verified as paid before you deliver it.

3. LEGAL DISCLAIMERS
   Keep the "Not legal advice" banner and the per-result disclaimer
   visible in your UI. Consider adding formal Terms of Service and a
   Privacy Policy page, and have a lawyer review your disclaimer
   language for your jurisdiction before commercial launch.

4. LIVE DATA SOURCES
   The USPTO and IP India integrations in this app are best-effort and
   will frequently fall back to the small internal reference list. If
   you want more reliable coverage, look into:
     - USPTO's official Trademark Status & Document Retrieval (TSDR)
       and bulk data products (some free, some paid) at
       https://developer.uspto.gov
     - Commercial trademark search APIs (e.g. Markify, Corsearch,
       Trademarkia) if your budget allows — these give far more
       complete and reliable coverage than free scraping.
   Do not present results from this app as a complete or certified
   search until you've upgraded the data sources.

5. PUBLISH IT AS A REAL WEBSITE (make it live for other people)
   -----------------------------------------------------------------
   Running "python app.py" only works on your own computer. To get a
   real, public URL, use a hosting provider. The easiest free option
   for a Flask app is Render. Steps:

   A) Put your project on GitHub
      1. Create a free account at https://github.com if you don't have
         one.
      2. Create a new repository (e.g. "markscout").
      3. Upload your whole project folder to it (app.py, requirements.txt,
         Procfile, templates/index.html, README.txt). GitHub's website
         has an "Add file → Upload files" button — no command line
         needed.

   B) Deploy on Render (free tier available)
      1. Go to https://render.com and sign up (you can sign up with
         your GitHub account).
      2. Click "New +" → "Web Service".
      3. Connect the GitHub repository you just created.
      4. Fill in:
           Runtime:        Python 3
           Build Command:  pip install -r requirements.txt
           Start Command:  gunicorn app:app
      5. Click "Create Web Service". Render will build and deploy it —
         this takes a few minutes the first time.
      6. When it's done, Render gives you a live URL like
         https://markscout.onrender.com — that's your public
         website. Share that link with anyone.

   C) Add a custom domain (optional)
      Once it's live on Render, you can point a domain you own
      (e.g. markscout.io) at it: in Render, go to your service →
      Settings → Custom Domain, and follow the DNS instructions shown.
      You'll need to buy the domain first from a registrar (Namecheap,
      GoDaddy, etc.).

   Alternatives to Render: Railway (railway.app) and PythonAnywhere
   (pythonanywhere.com) both work similarly and have free tiers — the
   Procfile included in this project (`web: gunicorn app:app --bind
   0.0.0.0:$PORT`) is what Railway also looks for automatically.

   Note: app.py currently runs with `debug=True` when you launch it
   locally with "python app.py" — that's fine for testing on your own
   machine, but gunicorn (used in production per the Procfile) does not
   use that debug mode, so this is already safe for deployment as-is.


TROUBLESHOOTING
----------------
- "python is not recognized": Python isn't on your PATH. Reinstall
  Python and make sure to check "Add Python to PATH" during setup.
- "ModuleNotFoundError: No module named 'flask'": Run
  "pip install -r requirements.txt" again inside the project folder.
- Page loads but styling looks broken: check your internet connection —
  the page loads Tailwind CSS from a CDN and needs internet access.
- Search always shows "unavailable" for live sources: this is expected
  behavior, not a bug — see STEP 5, item 4 above.
