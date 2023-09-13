# Hue-Bridge-Monitor

After I purchased my new Philips' Hue bridge and motion sensor I was quite disappointed of the features that were provided by the original Hue app: no historical data, no messaging. Even the integration with Apple's homekit wouldn't give me these features unless I had purchased additional and expensive hardware (I hadn't used homekit before and there was no Home pod or Apple TV in my home).

When I read about the Hue API and took a look at the documentation available from developers.meethue.com, I thought, "well, why not try and write myself a little pyhton app that would run on my raspberry pi?".

My primary use case was to place a motion sensor in my cottage to monitor the entrance hall while I was away. Although the cottage has a broadband connection I didn't want to use a polling method to query the state of the motion sensor remotely. Furthermore, the state of the motion sensor is reset 10 to 15 seconds after motion was detected, which would have required a polling interval of < 10 seconds and produced lots of useless data while actually no motion was detected.
Luckily, the V2 of the Hue API provides a method to listen to events received at the Hue bridge from connected devices. This includes state changes sent from the motion sensor. Even when there's no motion detected the motion sensor constantly tracks temperature and light level changes measured at its internal sensors (e.g. useful to monitor the room tempartaure during winter season). 

Instead of sending an alert message instantly after motion was detected, I've decided to collect the data and send a daily report as I sometimes rent the cottage to friends and guests which would lead to lots of false alerts. I'm still thinking of an elegant way to switch between instant allerting and data collection mode (or a combination of both). Also, to avoid frequent data transmission over the broadband connection, I've positioned the raspberry pi next to the Hue bridge in the cottage.

Currently, this python scipt is considered as a draft. If you'd like to use it for you own purposes, you'll have to customize a few settings in the code itself. Outputs are in German language but can be customized as well by tranlating the global string variables to the language of your choice.
