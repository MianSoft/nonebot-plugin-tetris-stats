import os
from asyncio import gather
from re import I
from typing import Any

import aiohttp
from nonebot import get_driver, on_regex
from nonebot.adapters.onebot.v11 import GROUP, MessageEvent
from nonebot.log import logger
from nonebot.matcher import Matcher
from ujson import JSONDecodeError, dumps, loads
from playwright.async_api import (
    Browser,
    Response,
    async_playwright
)

from ..utils.config import Config
from ..utils.sql import query_bind_info, write_bind_info
from ..utils.message_analyzer import (
    handle_bind_message,
    handle_stats_query_message
)


IOBind = on_regex(pattern=r'^io绑定|^iobind', flags=I, permission=GROUP)
IOStats = on_regex(pattern=r'^io查|^iostats', flags=I, permission=GROUP)

driver = get_driver()

config = Config.parse_obj(get_driver().config)


@IOBind.handle()
async def _(event: MessageEvent, matcher: Matcher):
    decoded_message = await handle_bind_message(message=event.raw_message, game_type='IO')
    if decoded_message[0] is None:
        await matcher.finish(decoded_message[1][0])
    if decoded_message[0] == 'ID':
        user_id_stats = await check_user_id(decoded_message[1][1])
        if user_id_stats[0] is False:
            await matcher.finish(user_id_stats[1])
        else:
            user_id = decoded_message[1][1]
    elif decoded_message[0] == 'Name':
        user_data = await get_user_data(user_name=decoded_message[1][1])
        if user_data[0] is False:
            await matcher.finish('用户信息请求失败')
        elif user_data[1] is False:
            await matcher.finish(f'用户信息请求错误:\n{user_data[2]["error"]}')
        else:
            user_id = await get_user_id(user_data[2])
    if event.sender.user_id is None:  # 理论上是不会有None出现的, ide快乐行属于是（
        logger.error('获取QQ号失败')
        await matcher.finish('获取QQ号失败')
    await matcher.finish(
        await write_bind_info(
            qq_number=event.sender.user_id,
            user=user_id,
            game_type='IO'
        )
    )


@IOStats.handle()
async def _(event: MessageEvent, matcher: Matcher):
    decoded_message = await handle_stats_query_message(message=event.raw_message, game_type='IO')
    if decoded_message[0] is None:
        await matcher.finish(decoded_message[1][0])
    elif decoded_message[0] == 'AT':
        if event.is_tome() is True:
            await matcher.finish('不能查询bot的信息')
        bind_info = await query_bind_info(qq_number=decoded_message[1][1], game_type='IO')
        if bind_info is None:
            message = '未查询到绑定信息'
        else:
            message = (f'* 由于无法验证绑定信息, 不能保证查询到的用户为本人\n{await generate_message(user_id=bind_info)}')
    elif decoded_message[0] == 'ME':
        if event.sender.user_id is None:
            logger.error('获取QQ号失败')
            await matcher.finish('获取QQ号失败, 请联系bot主人')
        bind_info = await query_bind_info(qq_number=event.sender.user_id, game_type='IO')
        if bind_info is None:
            message = '未查询到绑定信息'
        else:
            message = (f'* 由于无法验证绑定信息, 不能保证查询到的用户为本人\n{await generate_message(user_id=bind_info)}')
    elif decoded_message[0] == 'ID':
        message = await generate_message(user_id=decoded_message[1][1])
    elif decoded_message[0] == 'Name':
        message = await generate_message(user_name=decoded_message[1][1])
    await matcher.finish(message)


@driver.on_startup
async def _():
    await Request.init_cache()
    await Request.read_cache()


@driver.on_shutdown
async def _():
    await Request.close_browser()


async def get_user_data(
    user_name: str = None,
    user_id: str = None
) -> tuple[bool, bool, dict[str, Any]]:
    '''获取用户数据'''
    if user_name is not None and user_id is None:
        user_data_url = f'https://ch.tetr.io/api/users/{user_name}'
    elif user_name is None and user_id is not None:
        user_data_url = f'https://ch.tetr.io/api/users/{user_id}'
    else:
        raise ValueError('预期外行为, 请上报GitHub')
    return await Request.request(user_data_url)


async def get_solo_data(
    user_name: str = None,
    user_id: str = None
) -> tuple[bool, bool, dict[str, Any]]:
    '''获取Solo数据'''
    if user_name is not None and user_id is None:
        user_solo_url = f'https://ch.tetr.io/api/users/{user_name}/records'
    elif user_name is None and user_id is not None:
        user_solo_url = f'https://ch.tetr.io/api/users/{user_id}/records'
    else:
        raise ValueError('预期外行为, 请上报GitHub')
    return await Request.request(user_solo_url)


async def get_user_id(user_data: dict) -> str:
    '''获取用户ID'''
    return user_data['data']['user']['_id']


async def check_user_id(user_id: str) -> tuple[bool, str]:
    '''检查用户ID是否有效'''
    user_data = await get_user_data(user_id=user_id)
    if user_data[0] is False:
        return False, '用户信息请求失败'
    if user_data[1] is False:
        return False, f'用户信息请求错误:\n{user_data[2]["error"]}'
    if user_id == user_data[2]['data']['user']['_id']:
        return True, ''
    raise ValueError('服务器返回的userID和用户提供的不一致, 这种情况理论上不应该发生, 以防万一还是写一下（x')


async def get_league_stats(user_data: dict) -> dict[str, Any]:
    '''获取排位统计数据'''
    league = user_data['data']['user']['league']
    league_stats: dict[str, Any] = {}
    if league['gamesplayed'] != 0:
        league_stats['PPS'] = league['pps']
        league_stats['APM'] = league['apm']
        league_stats['VS'] = 0 if league['vs'] is None else league['vs']
        league_stats['Rank'] = 'Z' if league['rank'] == 'z' else league['rank'].upper()
        if league['rating'] == -1:
            league_stats['Rank'] = None
        else:
            league_stats['Rating'] = round(league['rating'], 2)
            league_stats['Glicko'] = round(league['glicko'], 2)
            league_stats['RD'] = round(league['rd'], 2)
        league_stats['Standing'] = league['standing']
        league_stats['LPM'] = round((league['pps'] * 24), 2)
        league_stats['APL'] = round(
            (league_stats['APM'] / league_stats['LPM']), 2)
        league_stats['ADPM'] = round((league_stats['VS'] * 0.6), 2)
        league_stats['ADPL'] = round(
            (league_stats['ADPM'] / league_stats['LPM']), 2)
    return league_stats


async def get_sprint_stats(solo_data: dict) -> dict[str, Any]:
    '''获取40L统计数据'''
    sprint_stats = {}
    solo = solo_data['data']['records']['40l']
    if solo['record'] is not None:
        sprint_stats['Time'] = round(
            solo['record']['endcontext']['finalTime'] / 1000, 2)
        if solo['rank'] is not None:
            sprint_stats['Rank'] = solo['rank']
    return sprint_stats


async def get_blitz_stats(solo_data: dict) -> dict[str, Any]:
    '''获取Blitz统计数据'''
    blitz_stats = {}
    blitz = solo_data['data']['records']['blitz']
    if blitz['record'] is not None:
        blitz_stats['Score'] = blitz['record']['endcontext']['score']
        if blitz['rank'] is not None:
            blitz_stats['Rank'] = blitz['rank']
    return blitz_stats


async def generate_message(user_name: str = None, user_id: str = None) -> str:
    '''生成消息'''
    user_data, solo_data = await gather(
        get_user_data(user_name=user_name, user_id=user_id),
        get_solo_data(user_name=user_name, user_id=user_id)
    )
    if user_data[0] is False:
        return '用户信息请求失败'
    if user_data[1] is False:
        return f'用户信息请求错误:\n{user_data[2]["error"]}'
    user_name = user_data[2]['data']['user']['username'].upper()
    league_stats = await get_league_stats(user_data[2])
    message = ''
    if not league_stats:
        message += f'用户 {user_name} 没有排位统计数据'
    else:
        if league_stats['Rank'] is None:
            message += f'用户 {user_name} 暂未完成定级赛, 最近十场的数据:'
        else:
            if league_stats['Rank'] == 'Z':
                message += f'用户 {user_name} 暂无段位, {league_stats["Rating"]} TR'
            else:
                message += f'{league_stats["Rank"]} 段用户 {user_name} {league_stats["Rating"]} TR (#{league_stats["Standing"]})'
            message += f', 段位分 {league_stats["Glicko"]}±{league_stats["RD"]}, 最近十场的数据:'
        message += f'\nL\'PM: {league_stats["LPM"]} ( {league_stats["PPS"]} pps )'
        message += f'\nAPM: {league_stats["APM"]} ( x{league_stats["APL"]} )'
        if league_stats["VS"] != 0:
            message += f'\nADPM: {league_stats["ADPM"]} ( x{league_stats["ADPL"]} ) ( {league_stats["VS"]}vs )'
    if solo_data[0] is False:
        return f'{message}\nSolo统计数据请求失败'
    if solo_data[1] is False:
        return f'{message}\nSolo统计数据请求错误:\n{solo_data[2]["error"]}'
    sprint_stats, blitz_stats = await gather(
        get_sprint_stats(solo_data[2]),
        get_blitz_stats(solo_data[2])
    )
    message += f'\n40L: {sprint_stats["Time"]}s' if 'Time' in sprint_stats else ''
    message += f' ( #{sprint_stats["Rank"]} )' if 'Rank' in sprint_stats else ''
    message += f'\nBlitz: {blitz_stats["Score"]}' if 'Score' in blitz_stats else ''
    message += f' ( #{blitz_stats["Rank"]} )' if 'Rank' in blitz_stats else ''
    return message


class Request:
    _browser: Browser | None = None
    _headers: dict | None = None
    _cookies: dict | None = None

    @classmethod
    async def _init_playwright(cls) -> Browser:
        '''初始化playwright'''
        playwright = await async_playwright().start()
        cls._browser = await playwright.firefox.launch()
        return cls._browser

    @classmethod
    async def _get_browser(cls) -> Browser:
        '''获取浏览器对象'''
        return cls._browser or await cls._init_playwright()

    @classmethod
    async def _anti_cloudflare(cls, url: str) -> tuple[bool, bool, dict[str, Any]]:
        '''用firefox硬穿五秒盾'''
        browser = await cls._get_browser()
        context = await browser.new_context()
        page = await context.new_page()
        response = await page.goto(url)
        attempts = 0
        while attempts < 60:
            attempts += 1
            text = await page.locator("body").text_content()
            if text is None:
                await page.wait_for_timeout(1000)
                continue
            if await page.title() == 'Please Wait... | Cloudflare':
                # TODO 有无人来做一个过验证码（
                break
            try:
                data = loads(text)
            except JSONDecodeError:
                await page.wait_for_timeout(1000)
            else:
                assert isinstance(response, Response)
                cls._headers = await response.request.all_headers()
                cls._cookies = {i['name']: i['value'] for i in await context.cookies()}
                await cls._write_cache()
                await page.close()
                await context.close()
                return True, data['success'], data
        await page.close()
        await context.close()
        return True, False, {'error': '绕过五秒盾失败'}

    @classmethod
    async def init_cache(cls) -> None:
        '''初始化缓存文件'''
        if not os.path.exists(os.path.dirname(config.cache_path)):
            os.makedirs(os.path.dirname(config.cache_path))
        if not os.path.exists(config.cache_path):
            with open(file=config.cache_path, mode='w', encoding='UTF-8') as file:
                file.write(
                    dumps(
                        {
                            'headers': cls._headers,
                            'cookies': cls._cookies
                        }
                    )
                )

    @classmethod
    async def read_cache(cls) -> None:
        '''读取缓存文件'''
        try:
            with open(file=config.cache_path, mode='r', encoding='UTF-8') as file:
                json = loads(file.read())
                cls._headers = json['headers']
                cls._cookies = json['cookies']
        except FileNotFoundError:
            await cls.init_cache()
        except PermissionError:
            os.remove(config.cache_path)
            await cls.init_cache()
        except JSONDecodeError:
            os.remove(config.cache_path)
            await cls.init_cache()

    @classmethod
    async def _write_cache(cls) -> None:
        '''写入缓存文件'''
        try:
            with open(file=config.cache_path, mode='r+', encoding='UTF-8') as file:
                file.write(
                    dumps(
                        {
                            'headers': cls._headers,
                            'cookies': cls._cookies
                        }
                    )
                )
        except FileNotFoundError:
            await cls.init_cache()
        except PermissionError:
            os.remove(config.cache_path)
            await cls.init_cache()
        except JSONDecodeError:
            os.remove(config.cache_path)
            await cls.init_cache()

    @classmethod
    async def request(cls, url: str) -> tuple[bool, bool, dict[str, Any]]:
        '''请求api'''
        try:
            async with aiohttp.ClientSession(cookies=cls._cookies) as session:
                async with session.get(url, headers=cls._headers) as resp:
                    data = await resp.json()
                    return True, data['success'], data
        except aiohttp.client_exceptions.ClientConnectorError as error:
            logger.error(f'请求错误\n{error}')
            return False, False, {}
        except aiohttp.client_exceptions.ContentTypeError:
            return await cls._anti_cloudflare(url)

    @classmethod
    async def close_browser(cls) -> None:
        '''关闭浏览器对象'''
        if isinstance(cls._browser, Browser):
            await cls._browser.close()
