"""decision_card 공정별 phrasing provider 5종.

FastAPI lifespan 안에서 명시적으로 register_phrasing_provider() 로 등록.
import time 등록 X (테스트 부팅 부작용 차단).

사용 예:
    from app.application.decisions.phrasing import register_phrasing_provider
    from app.application.decisions.phrasing_providers import (
        DefaultPhrasingProvider,
        SheathPhrasingProvider,
        StrandingPhrasingProvider,
        InsulationPhrasingProvider,
        OutsourcePhrasingProvider,
    )

    @app.on_event("startup")
    def _register():
        register_phrasing_provider(DefaultPhrasingProvider())
        register_phrasing_provider(SheathPhrasingProvider())
        register_phrasing_provider(StrandingPhrasingProvider())
        register_phrasing_provider(InsulationPhrasingProvider())
        register_phrasing_provider(OutsourcePhrasingProvider())
"""

from app.application.decisions.phrasing_providers.default import (
    DefaultPhrasingProvider,
)
from app.application.decisions.phrasing_providers.insulation import (
    InsulationPhrasingProvider,
)
from app.application.decisions.phrasing_providers.outsource import (
    OutsourcePhrasingProvider,
)
from app.application.decisions.phrasing_providers.sheath import (
    SheathPhrasingProvider,
)
from app.application.decisions.phrasing_providers.stranding import (
    StrandingPhrasingProvider,
)

__all__ = [
    "DefaultPhrasingProvider",
    "InsulationPhrasingProvider",
    "OutsourcePhrasingProvider",
    "SheathPhrasingProvider",
    "StrandingPhrasingProvider",
]
