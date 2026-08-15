"""DOI 数据源管理器——统一管理 Provider 注册、优先级与降级查询。"""

from backend.core.config import get_config
from backend.core.logger import get_logger
from backend.modules.bib_manager.models import DoiLookupResult
from backend.modules.bib_manager.providers.base import (
    BaseMetadataProvider,
    clean_doi,
    is_arxiv_doi,
)
from backend.modules.bib_manager.providers.crossref import CrossrefProvider
from backend.modules.bib_manager.providers.arxiv import ArxivProvider
from backend.modules.bib_manager.providers.datacite import DataCiteProvider
from backend.modules.bib_manager.providers.openalex import OpenAlexProvider
from backend.modules.bib_manager.providers.europepmc import EuropePMCProvider
from backend.modules.bib_manager.providers.semantic import SemanticScholarProvider


class ProviderManager:
    """DOI 元数据提供者管理器。

    职责：
        - 管理多个数据源提供者的注册和优先级
        - arXiv DOI 自动路由到 ArxivProvider
        - 普通 DOI：Crossref 主索引 → 补全层按优先级降级
    """

    def __init__(self) -> None:
        self._primary: BaseMetadataProvider | None = None
        self._fallbacks: list[BaseMetadataProvider] = []
        self._arxiv: ArxivProvider | None = None
        self._logger = get_logger("bib_manager.provider_manager")

    def register_primary(self, provider: BaseMetadataProvider) -> None:
        self._primary = provider
        self._logger.info(f"Primary provider registered: {provider.name}")

    def register_fallback(self, provider: BaseMetadataProvider) -> None:
        self._fallbacks.append(provider)
        self._logger.info(f"Fallback provider registered: {provider.name}")

    def register_arxiv(self, provider: ArxivProvider) -> None:
        self._arxiv = provider
        self._logger.info(f"arXiv provider registered: {provider.name}")

    async def close(self) -> None:
        """关闭所有已注册数据源持有的共享 HTTP 客户端。"""
        providers = [self._primary, self._arxiv, *self._fallbacks]
        for provider in providers:
            if provider is None:
                continue
            try:
                await provider.close()
            except Exception as e:
                self._logger.warning(
                    "Failed to close provider %s: %s", provider.name, e
                )

    def _merge(
        self, primary: DoiLookupResult, supplement: DoiLookupResult
    ) -> DoiLookupResult:
        """将补全层数据合并到主数据（仅填充空字段）。"""
        enhanced = primary.model_copy()
        mergeable = [
            "title",
            "abstract",
            "publisher",
            "isbn",
            "issn",
            "url",
            "volume",
            "issue",
            "pages",
        ]
        for field in mergeable:
            if not getattr(enhanced, field) and getattr(supplement, field):
                setattr(enhanced, field, getattr(supplement, field))

        if not enhanced.authors and supplement.authors:
            enhanced.authors = supplement.authors
        if not enhanced.year and supplement.year:
            enhanced.year = supplement.year
        if not enhanced.keywords and supplement.keywords:
            enhanced.keywords = supplement.keywords
        if not enhanced.journal and supplement.journal:
            enhanced.journal = supplement.journal

        return enhanced

    async def lookup_doi(self, doi: str) -> DoiLookupResult:
        """通过 DOI 查询元数据。

        策略：
        1. arXiv DOI → ArxivProvider
        2. 普通 DOI → Crossref（主索引）→ 补全层按优先级降级
        """
        clean = clean_doi(doi)
        if not clean:
            raise ValueError(f"Invalid DOI: {doi}")

        # arXiv DOI 特殊路由
        if is_arxiv_doi(clean):
            return await self._lookup_arxiv(clean)

        # 普通 DOI：主索引 + 降级
        return await self._lookup_normal(clean)

    async def _lookup_arxiv(self, doi: str) -> DoiLookupResult:
        if self._arxiv is None:
            raise RuntimeError("arXiv provider not registered")

        result = await self._arxiv.lookup_by_doi(doi)
        if result is None:
            raise ValueError(f"arXiv lookup failed for DOI: {doi}")
        return result

    async def _lookup_normal(self, doi: str) -> DoiLookupResult:
        # 1. 尝试主索引
        primary_result: DoiLookupResult | None = None
        if self._primary is not None:
            primary_result = await self._primary.lookup_by_doi(doi)
            if primary_result is not None:
                self._logger.info(
                    f"Primary lookup succeeded: {self._primary.name} / DOI: {doi}"
                )

        # 2. 按优先级尝试补全层，与主索引结果合并
        for provider in self._fallbacks:
            result = await provider.lookup_by_doi(doi)
            if result is not None:
                self._logger.info(
                    f"Fallback lookup succeeded: {provider.name} / DOI: {doi}"
                )
                if primary_result is not None:
                    primary_result = self._merge(primary_result, result)
                else:
                    primary_result = result
                break
            self._logger.warning(f"Fallback provider {provider.name} lookup failed")

        if primary_result is not None:
            return primary_result

        # 3. 所有数据源均失败
        raise ValueError(f"All providers failed for DOI: {doi}")


_provider_manager: ProviderManager | None = None


def reset_provider_manager() -> None:
    """重置全局 ProviderManager 单例，使配置变更立即生效。"""
    global _provider_manager
    _provider_manager = None


async def close_provider_manager() -> None:
    """关闭并释放全局 ProviderManager（服务关闭时调用）。"""
    global _provider_manager
    manager = _provider_manager
    _provider_manager = None
    if manager is not None:
        await manager.close()


def get_provider_manager() -> ProviderManager:
    """获取全局唯一的 ProviderManager 实例。"""
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = ProviderManager()
        cfg = get_config()
        pc = cfg.features.bib_manager.providers

        # arXiv 提供者
        if pc.arxiv.enabled:
            _provider_manager.register_arxiv(ArxivProvider())

        # 主索引
        if pc.crossref.enabled:
            _provider_manager.register_primary(CrossrefProvider())

        # 补全层（按配置中的 enabled 决定是否注册）
        def _register_if(provider_cls, enabled: bool):
            if enabled:
                _provider_manager.register_fallback(provider_cls())

        _register_if(DataCiteProvider, pc.datacite.enabled)
        _register_if(OpenAlexProvider, pc.openalex.enabled)
        _register_if(EuropePMCProvider, pc.europepmc.enabled)
        _register_if(SemanticScholarProvider, pc.semantic_scholar.enabled)

    return _provider_manager
