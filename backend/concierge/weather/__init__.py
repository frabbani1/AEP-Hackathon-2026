"""Real weather for the storm scenario: Open-Meteo client, summary, and the gust-to-severity mapping.

Weather is data from an outside source, shown as such. It never reaches the language model: the model prompts say
the model has no weather data, and the guards reject any draft that describes weather.
"""
