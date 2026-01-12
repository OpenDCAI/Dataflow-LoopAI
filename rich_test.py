# %%
# from rich.console import Console
# import time

# console = Console()

# text = ""
# for token in ["Hello", ",", " world", "!"]:
#     text += token
#     console.print(token, end="", style="cyan", soft_wrap=True)
#     time.sleep(0.3)

# console.clear()
# for token in ["Hello", ",", " I", " am", " refresher", "!"]:
#     text += token
#     console.print(token, end="", style="cyan", soft_wrap=True)
#     time.sleep(0.3)
# console.print("\n[bold yellow]Done![/bold yellow]")

from rich.console import Console
from rich.live import Live
from rich.text import Text
import time

console = Console()

with Live(console=console, refresh_per_second=4) as live:
    text = ""
    for token in ["Hello", ",", " world", "!"]:
        text += token
        render_text = Text(text, style="cyan")
        live.update(render_text)  # 动态更新显示内容
        time.sleep(0.3)
    
    text = ""
    for token in ["Hello", ",", " I", " am", " refresher", "!"]:
        text += token
        render_text = Text(text, style="cyan")
        live.update(render_text)  # 动态更新显示内容
        time.sleep(0.3)

console.print("[bold yellow]Done![/bold yellow]")
