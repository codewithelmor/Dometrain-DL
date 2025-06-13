import argparse
from bs4 import BeautifulSoup
import json
import logging
import os
import shutil
import re
import string
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import selenium.webdriver.support.expected_conditions as EC
import wget
import yt_dlp
from selenium.common import TimeoutException
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.webdriver import By
from selenium.webdriver.support.wait import WebDriverWait
from seleniumbase import Driver

video_attachments = []


def get_files_in_directory(directory):
    files = []
    # Walk through the directory tree
    for dirpath, _, filenames in os.walk(directory):
        # Append each file to the list
        for filename in filenames:
            files.append(os.path.join(dirpath, filename))
    return files


def create_folder(course_title):
    root_path = os.path.abspath(os.getcwd())
    course_path = os.path.join(root_path, "courses", course_title)
    os.makedirs(course_path, exist_ok=True)
    return course_path


def clean_string(data):
    logging.debug("Cleaning string: " + data)
    # Remove all non-ASCII characters (including emojis)
    data = data.encode('ascii', 'ignore').decode('ascii')
    # Replace specific characters with "-"
    data = data.replace(" ", "-").replace("\n", "").replace(":", "") \
        .replace("/", "").replace("|", "").replace("*", "").replace("?", "").replace("<", "") \
        .replace(">", "").replace("\"", "").replace("\\", "")
    return data.strip('-')


def truncate_title_to_fit_file_name(title, max_file_name_length=250):
    # the file name length should not be too long
    # truncate the title to accommodate the max used file extension length and lecture index prefix
    max_title_length = max_file_name_length - len(".mp4.part-Frag0000.part") - 3
    if len(title) > max_title_length:
        turncated_title = title[:max_title_length]
        logging.warning("Truncating title: " + turncated_title)
        return turncated_title
    return title


class DometrainDownloader:
    def __init__(self, verbose_arg=False, complete_lecture_arg=False, user_agent_arg=None, timeout_arg=10):
        self.driver = Driver(uc=True, headed=True)
        self.headers = {
            "User-Agent": user_agent_arg,
            "Origin": "https://player.hotmart.com",
            "Referer": "https://player.hotmart.com"
        }
        self.verbose = verbose_arg
        self._complete_lecture = complete_lecture_arg
        self.global_timeout = timeout_arg

    def check_elem_exists(self, by, selector, timeout):
        try:
            WebDriverWait(self.driver, timeout=self.global_timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except NoSuchElementException:
            return False
        except TimeoutException:
            return False
        except Exception:
            return False
        else:
            return True

    def bypass_cloudflare(self):
        if self.driver.capabilities["browserVersion"].split(".")[0] < "115":
            return
        logging.info("Bypassing cloudflare")
        time.sleep(1)
        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            try:
                self.driver.find_element(
                    By.ID, "challenge-stage"
                ).click()  # make sure the challenge is focused
                self.driver.execute_script(
                    '''window.open("''' + self.driver.current_url + """","_blank");"""
                )  # open page in new tab
                input(
                    "\033[93mWarning: Bypassing Cloudflare\nplease click on the captcha checkbox if not done already "
                    "and press enter to continue (do not close any of the tabs)\033[0m"
                )
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch to first tab
                self.driver.close()  # close first tab
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch back to new tab
            except Exception as e:
                logging.error("Could not bypass cloudflare: " + str(e))
                return
        else:
            logging.info("No need to bypass cloudflare")
            return

    def run(self, chapters, slug, dashboard, course_url, email, password, login_url, man_login_url):
        logging.info("Starting login")

        if man_login_url is None:
            # Check if login_url is not set
            if login_url is None:
                try:
                    self.find_login(course_url)
                except Exception as e:
                    logging.error("Could not find login: " + str(e), exc_info=self.verbose)
            else:
                self.driver.get(login_url)

            try:
                self.login(email, password)
            except Exception as e:
                logging.error("Could not login: " + str(e), exc_info=self.verbose)
                return
        else:
            self.driver.get(course_url)
            while self.driver.current_url != man_login_url:
                time.sleep(3)
                logging.info("Waiting for user to navigate to url: " + man_login_url)
                logging.info("Current url: " + self.driver.current_url)

        logging.info("Starting download of course: " + course_url)
        try:
            self.pick_course_downloader(chapters, slug, dashboard, course_url)
        except Exception as e:
            logging.error("Could not download course: " + course_url + " cause: " + str(e))

    def construct_sign_in_url(self, course_url):
        parsed_url = urlparse(course_url)
        # Replace the path with '/sign_in'
        sign_in_path = '/sign_in'
        fallback_url = urlunparse((parsed_url.scheme, parsed_url.netloc, sign_in_path, '', '', ''))
        return fallback_url

    def find_login(self, course_url):
        logging.info("Trying to find login")

        self.driver.implicitly_wait(self.global_timeout)
        self.driver.get(course_url)

        try:
            login_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.LINK_TEXT, "Login"))
            )
        except TimeoutException:
            logging.warning("Login button not found, navigating to fallback URL")
            fallback_url = self.construct_sign_in_url(course_url)
            self.driver.get(fallback_url)
        else:
            login_element.click()

    def login(self, email, password):
        logging.info("Logging in")

        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            self.bypass_cloudflare()

        time.sleep(10)

        WebDriverWait(self.driver, timeout=15).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        time.sleep(1)

        email_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "user[email]")))
        time.sleep(1)
        password_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "user[password]")))
        time.sleep(1)
        commit_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'button[type="submit"].button.button-primary.g-recaptcha[data-callback="onSubmit"]')))

        logging.debug("Filling in login form")
        email_element.click()
        email_element.clear()
        self.driver.execute_script("document.getElementById('user[email]').value='" + email + "'")

        time.sleep(1)

        password_element.click()
        password_element.clear()
        self.driver.execute_script("document.getElementById('user[password]').value='" + password + "'")

        time.sleep(1)

        commit_element.click()

        pause_seconds = 30
        time.sleep(pause_seconds)

    def pick_course_downloader(self, chapters, slug, dashboard, course_url):
        # Check if we are already on the course page
        if not course_url in str(self.driver.current_url):
            logging.info("Switching to course page")
            self.driver.get(course_url)
            if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
                self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=self.global_timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # https://support.teachable.com/hc/en-us/articles/360058715732-Course-Design-Templates
        logging.info("Picking course downloader")
        if self.driver.find_elements(By.CSS_SELECTOR, 'button[data-qa="complete-continue__btn"].brand-color__background.brand-color__dynamic-text._button--default--small_142a8m._button--icon-right--small_142a8m[data-ember-action=""]'):
            logging.info('Choosing __next format')
            self.download_course_simple(chapters, slug, dashboard, course_url)

            # move attachmets to folders
            if len(video_attachments) > 0:
                root_path = os.path.abspath(os.getcwd())
                downloaded_files_path = os.path.join(root_path, "downloaded_files")
                files_list = get_files_in_directory(downloaded_files_path)
                for file in files_list:
                    for video_attachment in video_attachments:
                        if video_attachment["file_name"] in file:
                            file_path = os.path.join(video_attachment["file_path"], video_attachment["file_name"])
                            # Move the file to the destination directory
                            shutil.move(file, file_path)
                            break
        else:
            logging.error("Downloader does not support this course template. Please open an issue on github.")
    
    def get_course_title_next(self, course_url):
        if not course_url in str(self.driver.current_url):
            self.driver.get(course_url)

        title = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.course-progress__title')))
        course_title = title.accessible_name

        course_title = clean_string(course_title)
        return course_title

    def download_course_simple(self, chapters, slug, dashboard, course_url):
        self.driver.implicitly_wait(2)
        logging.info("Detected next course format")
        course_title = self.get_course_title_next(course_url)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title)

        output_file = os.path.join(course_path, "course.html")
        try:
            with open(output_file, 'w+', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        chapters_array = []
        if len(chapters.strip()) > 0:
            if chapters != "all":
                chapters_array = [int(chapter.strip()) for chapter in chapters.split(",")]

        chapter_idx = 0
        video_list = []
        slim_sections = self.driver.find_elements(By.CSS_SELECTOR, ".course-player__chapters-item")
        for slim_section in slim_sections:
            chapter_idx += 1

            if len(chapters_array) > 0:
                if chapter_idx not in chapters_array:
                    continue
            
            ul_elem = slim_section.find_element(By.TAG_NAME, "ul")
            bars = ul_elem.find_elements(By.CLASS_NAME, 'course-player__content-item')
            chapter_title = slim_section.find_element(By.CSS_SELECTOR, "._chapter-item__title_d57kmg").accessible_name
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            try:
                not_available_element = WebDriverWait(slim_section, self.global_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".drip-tag")))
                logging.warning('Chapter "%s" not available, skipping', chapter_title)
                continue
            except TimeoutException:
                logging.info("Chapter is available")
                pass  # Element wasn't found so the chapter is available

            chapter_title = str(chapter_title).strip('-')
            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            idx = 1
            for bar in bars:
                time.sleep(5)
                video = bar.find_element(By.CSS_SELECTOR, ".course-player__content-item__link")
                
                link = video.get_attribute("href")

                video_title = video.find_element(By.CLASS_NAME, "content-item__title")
                video_title_html = video_title.get_attribute("outerHTML")
                video_title_soup = BeautifulSoup(video_title_html, 'html.parser')
                title_text = video_title_soup.get_text()
                title_text_split = title_text.split()
                # Find index of "Video"
                title_text_split_video_index = title_text_split.index("Video")
                # Concatenate items before "Video"
                title_raw = " ".join(title_text_split[:title_text_split_video_index])
                title = clean_string(title_raw)
                title = str(title).strip('-')

                logging.info("Found lecture: " + title)
                truncated_title = truncate_title_to_fit_file_name(title)
                video_entity = {"link": link, "title": truncated_title, "idx": idx, "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        # Download course image
        self.download_cover(course_path, slug, dashboard)

        # Download course videos
        self.download_videos_from_links(video_list)
    
    def download_cover(self, course_path, slug, dashboard):
        if self.driver.current_url != dashboard:
            logging.info("Navigating to dashboard: " + dashboard)
            self.driver.get(dashboard)
            
            time.sleep(10)
        
        div_covers = self.driver.find_elements(By.CLASS_NAME, 'cat-course-card')
        
        for div_cover in div_covers:
            div_cover_href = div_cover.get_attribute("data-href")
            if slug in div_cover_href:
                img_cover = div_cover.find_element(By.TAG_NAME, 'img')
                image_link = img_cover.get_attribute("src")

                # Download course image
                try:
                    logging.info("Downloading course image")
                    # Save image
                    image_path = os.path.join(course_path, "course-image.jpg")
                    # send a GET request to the image link
                    try:
                        response = requests.get(image_link)
                        # write the image data to a file
                        with open(image_path, "wb") as f:
                            f.write(response.content)
                        # print a message indicating that the image was downloaded
                        logging.info("Image downloaded successfully.")
                        break
                    except Exception as e:
                        # print a message indicating that the image download failed
                        logging.warning("Failed to download image:" + str(e))
                except Exception as e:
                    logging.warning("Could not find course image: " + str(e))
                    pass
                            
            # else:
            #     print("No background image URL found.")

    def download_videos_from_links(self, video_list):
        for video in video_list:
            if self.driver.current_url != video["link"]:
                logging.info("Navigating to lecture: " + video["title"])
                self.driver.get(video["link"])
                self.driver.implicitly_wait(self.global_timeout)
            logging.info("Downloading lecture: " + video["title"])

            time.sleep(10)

            # try:                
            #     video_title_raw = self.driver.find_elements(By.CLASS_NAME, 'course-player__content-header__title')[0]
            #     video_title_raw_html = video_title_raw.get_attribute("outerHTML")
            #     video_title_raw_html_soup = BeautifulSoup(video_title_raw_html, 'html.parser')
            #     video["title"] = clean_string(video_title_raw_html_soup.get_text())
            # except Exception as e:
            #     pass

            try:
                logging.info("Saving html")
                self.save_webpage_as_html(video["title"], video["idx"], video["download_path"])
            except Exception as e:
                logging.error("Could not save html: " + video["title"] + " cause: " + str(e), exc_info=self.verbose)

            try:
                logging.info("Downloading attachments")
                self.download_attachments(video["link"], video["title"], video["idx"], video["download_path"])
            except Exception as e:
                logging.warning("Could not download attachments: " + video["title"] + " cause: " + str(e))

            video_iframes = self.driver.find_elements(By.XPATH, "//iframe[@title='Video Lesson']")

            for i, iframe in enumerate(video_iframes):
                try:
                    logging.info("Switching to video frame")
                    self.driver.switch_to.frame(iframe)

                    #w-json-ldwistia_26
                    script_text = self.driver.find_element(By.ID, "w-json-ldwistia_40")
                    json_text = json.loads(script_text.get_attribute("innerHTML"))
                    link = json_text['contentUrl']

                    # Append -n to the video title if there are multiple iframes
                    video_title = video["title"] + ("-" + str(i + 1) if len(video_iframes) > 1 else "")

                    try:
                        logging.info("Downloading subtitle")
                        self.download_subtitle(link, video_title, video["idx"], video["download_path"])
                    except Exception as e:
                        logging.warning("Could not download subtitle: " + video_title + " cause: " + str(e))

                    try:
                        logging.info("Downloading video")
                        self.download_video(link, video_title, video["idx"], video["download_path"])
                    except Exception as e:
                        logging.warning("Could not download video: " + video_title + " cause: " + str(e))

                    self.driver.switch_to.default_content()  # Switch back to main content before the next iteration

                except Exception as e:
                    logging.warning("Could not find video: " + video["title"])
                    continue

            logging.info("Downloaded video: " + video["title"])

            if self._complete_lecture:
                try:
                    logging.info("Completing lecture")
                    self.complete_lecture()
                except Exception as e:
                    logging.warning("Could not complete lecture: " + video["title"] + " cause: " + str(e))

        return

    def complete_lecture(self):
        # Complete lecture
        self.driver.switch_to.default_content()
        complete_button = self.driver.find_element(By.ID, "lecture_complete_button")
        if complete_button:
            logging.info("Found complete button")
            complete_button.click()
            logging.info("Completed lecture")
            time.sleep(3)

    def download_video(self, link, title, video_index, output_path):
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "concurrentfragments": 15,
            "outtmpl": os.path.join(output_path, "{:02d}-{}.mp4".format(video_index, title)),
            "verbose": self.verbose,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([link])
        except Exception as e:
            logging.error("Could not download video: " + title + " cause: " + str(e))

    # This function is needed because yt-dlp subtitle downloader is not working
    def download_subtitle(self, link, title, video_index, output_path):
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "allsubtitles": True,
            "subtitleslangs": ["all"],
            "concurrentfragments": 10,
            "writesubtitles": True,
            "outtmpl": os.path.join(output_path, title),
            "verbose": self.verbose,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(link, download=False)
                info_json = ydl.sanitize_info(info)
        except Exception as e:
            logging.warning("Could not download subtitle: " + title + " cause: " + str(e))

        subtitle_links = {}
        for lang, sub_info in info_json["requested_subtitles"].items():
            subtitle_links[lang] = {"url": sub_info["url"], "ext": sub_info["ext"]}

        # Print the subtitle links and language names
        req = None
        for lang, sub in subtitle_links.items():
            subtitle_filename = "{:02d}-{}.{}.{}".format(video_index, title, lang, sub["ext"])
            file_path = os.path.join(output_path, subtitle_filename)
            if os.path.isfile(file_path):
                logging.info("Skipping existing subtitle: " + subtitle_filename)
            else:
                base_url = sub["url"]
                try:
                    req = requests.get(sub["url"], headers=self.headers)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                relative_path = req.text.split("\n")[5]
                full_url = urljoin(base_url, relative_path)
                try:
                    response = requests.get(full_url, headers=self.headers)
                    with open(file_path, "wb") as f:
                        f.write(response.content)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                logging.info("Downloaded subtitle: " + subtitle_filename)

    def download_attachments(self, link, title, video_index, output_path):
        video_title = "{:02d}-{}".format(video_index, title)

        try:
            ul_attachment = self.driver.find_element(By.CLASS_NAME, 'course-player__download-files__list')
            if ul_attachment:
                file_output_path = os.path.join(output_path, video_title)
                os.makedirs(file_output_path, exist_ok=True)

                li_attachments = ul_attachment.find_elements(By.TAG_NAME, 'li')
                for li_attachment in li_attachments:
                    attachment_link = li_attachment.find_element(By.TAG_NAME, 'a')
                    attachment_link.click()
                    
                    attachment_title = li_attachment.find_element(By.CLASS_NAME, 'course-player__download-files__label')
                    attachment_title_final = attachment_title.text
                    video_attachments.append({ "file_path": file_output_path, "file_name": attachment_title_final})
        except Exception as err:
            pass

    def save_webpage_as_html(self, title, video_index, output_path):
        output_file = os.path.join(output_path, "{:02d}-{}.html".format(video_index, title))
        with open(output_file, 'w+', encoding='utf-8') as f:
            f.write(self.driver.page_source)
        logging.info("Saved webpage as html: " + output_file)

    def save_webpage_as_pdf(self, title, video_index, output_path):
        output_file_pdf = os.path.join(output_path, "{:02d}-{}.pdf".format(video_index, title))
        self.driver.save_print_page(output_file_pdf)
        logging.info("Saved webpage as pdf: " + output_file_pdf)

    def clean_up(self):
        logging.info("Cleaning up")
        self.driver.quit()
        # Delete cookies.txt
        if os.path.exists("cookies.txt"):
            os.remove("cookies.txt")


def read_urls_from_file(file_path):
    urls = []
    try:
        with open(file_path, 'r') as file:
            urls = file.read().splitlines()
    except FileNotFoundError:
        logging.error(f"File not found: {file_path}")
    except IOError as e:
        logging.error(f"IOError reading file: {file_path}. Error: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error reading file: {file_path}. Error: {str(e)}")

    if urls:
        logging.info(f"Successfully read {len(urls)} URLs from file: {file_path}")
    else:
        logging.warning(f"No URLs found in file: {file_path}")

    return urls


def check_required_args(args):
    if args.email and args.password:
        return True
    elif args.man_login_url:
        return True
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Dometrain-Dl', description='Download courses', )
    parser.add_argument("--chapters", required=False, default='all', help='Specific chapters of the course to download e.g. 1,2')
    parser.add_argument("--slug", required=False, help='Slug of the course')
    parser.add_argument("--dashboard", required=False, help='Dashboard of the site')
    parser.add_argument("--url", required=False, help='URL of the course')
    parser.add_argument("-e", "--email", required=False, help='Email of the account')
    parser.add_argument("-p", "--password", required=False, help='Password of the account')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity level (repeat for more verbosity)')
    parser.add_argument('--complete-lecture', action='store_true', default=False,
                        help='Complete the lecture after downloading')
    parser.add_argument("--login_url", required=False, help='(Optional) URL to teachable SSO login page')
    parser.add_argument("--man_login_url", required=False,
                        help='Login manually and start downloading when this url is reached')
    parser.add_argument("--user-agent", required=False, help='User agent to use when downloading videos',
                        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/116.0.0.0 Safari/537.36")
    parser.add_argument("-t", "--timeout", required=False, help='Timeout for selenium driver', default=10)
    args = parser.parse_args()
    verbose = False
    if args.verbose == 0:
        log_level = logging.WARNING
    elif args.verbose == 1:
        log_level = logging.INFO
    else:
        verbose = True
        log_level = logging.DEBUG

    logging.basicConfig(level=log_level, format='%(levelname)s: %(message)s')

    if not check_required_args(args):
        logging.error("Required arguments are missing. Choose email/password or manual login (man_login_url).")
        exit(1)

    downloader = DometrainDownloader(verbose_arg=verbose, complete_lecture_arg=args.complete_lecture,
                                     user_agent_arg=args.user_agent, timeout_arg=args.timeout)

    # Check if url argument is passed
    if not args.url:
        logging.error("URL is required")
        sys.exit(1)
    try:
        downloader.run(chapters=args.chapters, slug=args.slug, dashboard=args.dashboard, course_url=args.url, email=args.email, password=args.password, login_url=args.login_url,
                        man_login_url=args.man_login_url)
        downloader.clean_up()
        sys.exit(0)
    except KeyboardInterrupt:
        logging.error("Interrupted by user")
        downloader.clean_up()
        sys.exit(1)
    except Exception as e:
        logging.error("Error: " + str(e))
        downloader.clean_up()
        sys.exit(1)
