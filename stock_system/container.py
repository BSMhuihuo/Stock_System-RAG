from __future__ import annotations

from .db import init_db
from .services.feishu import FeishuService
from .services.market_data import MarketDataService
from .services.ranking import RankingService
from .services.research import ResearchService
from .services.system_settings import SystemSettingsService
from .services.trading import TradingService


class ServiceContainer:
    def __init__(self) -> None:
        init_db()
        self.market_data = MarketDataService()
        self.research = ResearchService()
        self.system_settings = SystemSettingsService()
        self.ranking = RankingService(self.market_data, self.research)
        self.feishu = FeishuService()
        self.trading = TradingService(self.market_data, self.ranking, self.research, self.system_settings)


container = ServiceContainer()
