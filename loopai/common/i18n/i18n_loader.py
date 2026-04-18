from .i18n_dict import i18n_dict


class I18NLoader:
    def __init__(self, lang: str = "en"):
        self.lang = lang
    
    def load_langauge_from_state(self, state: dict) -> None:
        self.lang = state.get("language", self.lang)

    def __call__(self, key: str) -> str:
        if key not in i18n_dict:
            return key
        return i18n_dict[key].get(self.lang, key)

