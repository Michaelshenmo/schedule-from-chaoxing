import re


def parse_weeks(value, max_week: int, week_type=None):
    if isinstance(value, (list, tuple, set)):
        numbers = set()
        for item in value:
            try:
                numbers.add(int(item))
            except (TypeError, ValueError):
                continue
    else:
        text = str(value or "").strip()
        if not text:
            return set()
        numbers = set()
        for token in re.findall(r"\d+\s*[-~—至]\s*\d+|\d+", text):
            if re.search(r"[-~—至]", token):
                start, end = re.split(r"[-~—至]", token, maxsplit=1)
                try:
                    start_number = int(start)
                    end_number = int(end)
                except ValueError:
                    continue
                if start_number > end_number:
                    start_number, end_number = end_number, start_number
                numbers.update(range(start_number, end_number + 1))
            else:
                try:
                    numbers.add(int(token))
                except ValueError:
                    continue
        if "单" in text:
            numbers = {number for number in numbers if number % 2 == 1}
        elif "双" in text:
            numbers = {number for number in numbers if number % 2 == 0}
        elif str(week_type) in ("0", "1"):
            parity = 1 if str(week_type) == "0" else 0
            numbers = {number for number in numbers if number % 2 == parity}
    return {number for number in numbers if 1 <= number <= max_week}
