import os, re, time, threading
import requests
from tqdm import tqdm


# 不使用系统代理
proxies = {"http": None, "https": None}

# 多线程下载器
class MultiDownloader:
    def __init__(self, headers, url, file_name, thread_count=20):
        self.headers = headers
        self.url = url
        self.file_name = file_name
        self.thread_count = thread_count
        self.chunk_size = 1024 * 1024
        self.total_range = self.get_file_size()
        self.file_lock = threading.Lock()
        self.user_choice = None
        self.interrupt = False

    def get_file_size(self):
        res = requests.head(self.url, headers=self.headers, proxies=proxies)
        if res.status_code == 200:
            return int(res.headers.get('Content-Length'))
        return None

    def page_dispatcher(self, content_size):
        page_size = content_size // self.thread_count
        start_pos = 0
        while start_pos + page_size < content_size:
            yield {
                'start_pos': start_pos,
                'end_pos': start_pos + page_size
            }
            start_pos += page_size + 1
        yield {
            'start_pos': start_pos,
            'end_pos': content_size - 1
        }

    def download_range(self, page, file_handler):
        range_headers = {"Range": f"bytes={page['start_pos']}-{page['end_pos']}"}
        range_headers |= self.headers
        try_times = 3
        for _ in range(try_times):
            with requests.get(url=self.url, headers=range_headers, stream=True, timeout=30, proxies=proxies) as res:
                if res.status_code == 206:
                    for data in res.iter_content(chunk_size=self.chunk_size):
                        with self.file_lock:
                            file_handler.seek(page["start_pos"])
                            file_handler.write(data)
                        page["start_pos"] += len(data)
                    break

    def get_user_choice(self):
        self.user_choice = input(f"{self.file_name.split('/')[0]}已存在,是否覆盖(Y/n)?")
        self.interrupt = True
    
    def run(self):
        if not self.total_range or self.total_range < 1024:
            raise Exception("get file total size failed")
        
        timeout = 10
        if os.path.exists(self.file_name.split('/')[0]):
            thread = threading.Thread(target=self.get_user_choice)
            thread.start()
            for i in range(timeout, 0, -1):
                if self.interrupt:
                    break
                print(f"倒计时 {i} 秒...\r", end='')
                time.sleep(1)
            thread.join(timeout)
            if self.user_choice is None:
                self.user_choice = 'y'
            if self.user_choice == 'y' or self.user_choice == 'Y' or self.user_choice == '':
                print(f"开始覆盖 {self.file_name.split('/')[0]}")
            else:
                return
        else:
            os.mkdir(self.file_name.split('/')[0])

        thread_list = []
        with open(self.file_name, "wb+") as f:
            for i, page in enumerate(self.page_dispatcher(self.total_range)):
                thread_list.append(threading.Thread(target=self.download_range, args=(page, f)))
            for thread in thread_list:
                thread.start()
            for thread in thread_list:
                thread.join()


# 会议下载器
class MeetingDownloader:
    def __init__(self, cookie):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36',
            'cookie': cookie,
            'bv-csrf-token': cookie[cookie.find('bv_csrf_token=') + len('bv_csrf_token='):cookie.find(';', cookie.find('bv_csrf_token='))],
            'referer': f'https://meetings.feishu.cn/minutes/me',
            'content-type': 'application/x-www-form-urlencoded'
        }
        if len(self.headers.get('bv-csrf-token')) != 36:
            raise Exception("cookie中不包含bv_csrf_token，请确保从请求`list?size=20&`中获取！")
        
    def get_meeting_info(self):
        """
        批量获取妙记信息

        size的取值：获取的妙记数量

        space_name的取值：
        1：主页（包含企业内部妙记与外部妙记）
        2：我的内容（只包含归属人为自己的妙记）
        """
        get_rec_url = f"https://meetings.feishu.cn/minutes/api/space/list?&size=1000&space_name=2"
        resp = requests.get(url=get_rec_url, headers=self.headers, proxies=proxies)
        return list(reversed(resp.json()['data']['list'])) # 返回按时间正序排列的妙记信息（从旧到新）

    def download_video(self, minutes_info):
        """
        下载单个妙记视频
        """
        # 获取妙记视频的下载链接
        video_url_url = f"https://meetings.feishu.cn/minutes/api/status?object_token={minutes_info['object_token']}&language=zh_cn&_t={int(time.time() * 1000)}"
        resp = requests.get(url=video_url_url, headers=self.headers, proxies=proxies)
        video_url = resp.json()['data']['video_info']['video_download_url']

        # 根据会议的起止时间和会议标题来设置文件名
        start_time = time.strftime("%Y年%m月%d日%H时%M分", time.localtime(minutes_info['start_time'] / 1000))
        stop_time = time.strftime("%Y年%m月%d日%H时%M分", time.localtime(minutes_info['stop_time'] / 1000))
        file_name = start_time+"至"+stop_time+minutes_info['topic']

        # 将文件名中的特殊字符替换为下划线
        rstr = r"[\/\\\:\*\?\"\<\>\|]"  # '/ \ : * ? " < > |'
        file_name = re.sub(rstr, "_", file_name)

        # 多线程下载
        run_params = {'headers': self.headers,
                        'url': video_url,
                        'file_name': f'{file_name}/{file_name}.mp4',
                        'thread_count': 20
                        }
        downloader = MultiDownloader(**run_params)
        downloader.run()

        return file_name

    def download_subtitle(self, object_token, file_name, file_mtime):
        """
        下载单个妙记字幕
        """
        srt_url = f"https://meetings.feishu.cn/minutes/api/export"
        params = {'add_speaker': 'true', # 包含说话人
                    'add_timestamp': 'true', # 包含时间戳
                    'format': '3', # SRT格式
                    'object_token': object_token, # 妙记id
                    }
        resp = requests.post(url=srt_url, params=params, headers=self.headers, proxies=proxies)

        # 如果cookie选择的不对，可能会出现能下载视频但无法下载字幕的情况
        if resp.status_code != 200:
            raise Exception(f"下载字幕失败，请检查你的cookie！\nStatus code: {resp.status_code}")
        
        # 写入对应视频的文件夹
        resp.encoding = "utf-8"
        with open(f"{file_name}/{file_name}.srt", "w+", encoding='utf-8') as f:
            f.write(resp.text)

        # 将文件最后修改时间改为会议结束时间
        os.utime(f"{file_name}/{file_name}.srt", (file_mtime, file_mtime))
        os.utime(f"{file_name}/{file_name}.mp4", (file_mtime, file_mtime))
        os.utime(f"{file_name}", (file_mtime, file_mtime))

    def check_meetings(self):
        """
        检查需要下载的会议
        """
        all_meetings = self.get_meeting_info()
        need_download_meetings = []

        # 检查记录中不存在的会议id进行下载
        if os.path.exists('meetings.txt'):
            with open('meetings.txt', 'r') as f:
                downloaded_meetings = f.readlines()
            need_download_meetings = [index for index in all_meetings if index['meeting_id']+'\n' not in downloaded_meetings]
        else:
            need_download_meetings = all_meetings
        # 如果有需要下载的会议则进行下载
        if need_download_meetings:
            for index in tqdm(need_download_meetings, desc='Downloading Meetings', unit=' meeting'):
                # 下载妙记视频
                file_name = self.download_video(index)
                # 下载妙记字幕
                self.download_subtitle(index['object_token'], file_name, index['stop_time']/1000)
                # 将已下载的妙记所对应的会议id记录到文件中
                with open('meetings.txt', 'a+') as f:
                    f.write(index['meeting_id'] + '\n')

    def delete_minutes(self, num):
        """
        删除指定数量的最早几个妙记
        """
        all_meetings = self.get_meeting_info()
        num = num if num <= len(all_meetings) else 1
        need_delete_meetings = all_meetings[:num]

        for index in tqdm(need_delete_meetings, desc='Deleting Meetings', unit=' meeting'):
            
            # 将该妙记放入回收站
            delete_url = f"https://meetings.feishu.cn/minutes/api/space/delete"
            params = {'object_tokens': index['object_token'],
                        'is_destroyed': 'false',
                        'language': 'zh_cn'}
            resp = requests.post(url=delete_url, params=params, headers=self.headers, proxies=proxies)
            if resp.status_code != 200:
                raise Exception(f"删除会议{index['meeting_id']}失败！{resp.json()}")
            
            # 将该妙记彻底删除
            params['is_destroyed'] = 'true'
            resp = requests.post(url=delete_url, params=params, headers=self.headers, proxies=proxies)
            if resp.status_code != 200:
                raise Exception(f"删除会议{index['meeting_id']}失败！{resp.json()}")


if __name__ == '__main__':

    # 在飞书妙记主页 https://meetings.feishu.cn/minutes/home 获取cookie
    minutes_cookie = ""

    # （可选，需身份为企业创建人、超级管理员或普通管理员）在飞书管理后台获取cookie
    manager_cookie = ""

    if not minutes_cookie:
        raise Exception("cookie不能为空！")
    
    # 如果未填写管理参数，则定时下载会议
    elif not manager_cookie:
        while True:
            print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            downloader = MeetingDownloader(minutes_cookie)
            downloader.check_meetings()
            downloader.delete_minutes(1)
            time.sleep(3600)

    # 如果填写了管理参数，则定时查询妙记空间使用情况，超出指定额度则删除最早的指定数量的会议
    else :
        # 从manager_cookie中获取X-Csrf-Token
        x_csrf_token = manager_cookie[manager_cookie.find(' csrf_token=') + len(' csrf_token='):manager_cookie.find(';', manager_cookie.find(' csrf_token='))]
        if len(x_csrf_token) != 36:
            raise Exception("manager_cookie中不包含csrf_token，请确保从请求`count?_t=`中获取！")

        usage_bytes_old = 0 # 上次记录的已经使用的字节数

        # 定期查询已使用的妙记空间字节数
        while True:
            print(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

            # 查询妙记空间已用字节数
            query_url = f"https://www.feishu.cn/suite/admin/api/gaea/usages"
            manager_headers = {'cookie': manager_cookie, 'X-Csrf-Token':x_csrf_token}
            res = requests.get(url=query_url, headers=manager_headers, proxies=proxies)
            usage_bytes = int(res.json()['data']['items'][6]['usage']) # 查询到的目前已用字节数
            print(f"已用空间：{usage_bytes / 2 ** 30:.2f}GB")

            # 如果已用字节数有变化则下载会议
            if usage_bytes != usage_bytes_old:
                downloader = MeetingDownloader(minutes_cookie)
                downloader.check_meetings()
                # 如果已用超过9.65G则删除最早的两个会议
                if usage_bytes > 2 ** 30 * 9.65:
                    downloader.delete_minutes(2)
            usage_bytes_old = usage_bytes #　更新已用字节数
            time.sleep(3600)
