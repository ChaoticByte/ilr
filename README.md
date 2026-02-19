# Image based Load Remover for LibreSplit

This is an experimental image-based Load Remover compatible with LibreSplit.


## Notes

- This works only in games where a distinct region, e.g. a text is present at exactly the same position during the loading screen
- Wayland is not supported ([see this issue in python-mss](https://github.com/BoboTiG/python-mss/issues/155))!


## Compatibility

### System Requirements

- Tested on Ubuntu 24.04
- Python 3.12
- X11

You also need the pip packages in `requirements.txt`!

### Games

You may have to write your own profile. Or tweak existing ones when you have another screen resolution or ingame settings.

List of predefined profiles:

- [Horizon Zero Dawn](./profiles/hzd/)
- [Horizon Zero Dawn Remastered *](./profiles/hzdr/)
- [Fallout 4 *](./profiles/fo4/)

\* inaccurate or error-prone


## Create a profile

Take a look at this example profile:

```yml
# Path to the reference images, relative to the profile file:
references:
  - image: ./loading.png
    # You can also specify a mask to ignore areas of
    # the reference image & screenshots (this is optional):
    mask: ./loading-mask.png
# starts with 1
monitor: 1
# The region containing something that is always
# there in the loading screen - this should be
# as distinct as possible to mitigate false-positives
region:
  left: 98
  top: 975
  width: 118
  height: 25
# How to calculate the difference of the
# current frame against the reference
difference:
  # The only existing method currently is
  # nrmse (Normalized Root Mean Square Error)
  method: nrmse
  # If the difference < this threshold, the current
  # frame is considered a loading screen
  threshold: 0.02
# How many detection to perform per second (ideally)
# optional, default: 30
target_dps: 30
# Pre-defined filters might help to improve detection accuracy
filters:
  # Converts the screenshots from RGB to greyscale
  # by calculating the mean value of r, g and b
  - mean_greyscale
```

You can dump cropped screenshots in 1-second intervals using the `dump-images` command, and use one of those as the reference image.  
Run the `dump-difference` command to debug the difference to the reference image, for every frame.
