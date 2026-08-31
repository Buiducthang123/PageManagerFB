

# Get Facebook Videos using the Facebook Video API

This document shows you how to get [Video data, such as IDs, description, and updated times,](https://developers.facebook.com/docs/graph-api/reference/video) for videos published on Facebook Pages or User Feeds.

## Get Page Videos

### Before You Start

For Pages on which you are able to perform the [`MANAGE` task](https://developers.facebook.com/docs/graph-api/reference/page/videos), you will need:

* A Page access token requested by a person who can perform the `MANAGE` task on the Page
* The [`pages_read_engagement` permission](https://developers.facebook.com/docs/permissions/reference/pages_read_engagement)

For published Pages, which you are not able to perform the [`MANAGE` task](https://developers.facebook.com/docs/graph-api/reference/page/videos), you will need:

* A User access token
* The [Page Public Content Access](https://developers.facebook.com/docs/apps/review/feature#reference-PAGES_ACCESS)

Send a `GET` request to the `/<PAGE_ID>/videos` endpoint to get a list of all videos of a Page.

```curl
curl -i -X GET "https://graph.facebook.com/<PAGE_ID>/videos?access_token=<PAGE_ACCESS_TOKEN>"
```

On success, your app receives the following response:  

```json
{
  "data": [
    {
      "description": "Clouds",
      "updated_time": "2019-09-25T17:18:30+0000",
      "id": "2153206464921154"
    },
    {
      "updated_time": "2020-03-26T23:45:11+0000",
      "id": "2232477747039197"
    },
    ...
  ],
  "paging": {
    "cursors": {
      "before": "MjE1MzIwNjQ2NDkyMTE1NAZDZD",
      "after": "MTQwOTU5MTg4NTc2MzM0MwZDZD"
    }
  }
}
```

## Get User Videos

### Before You Start

You will need:

* A User access token requested by the User who owns the video
* The [`user_videos` permission](https://developers.facebook.com/docs/permissions/reference/user_videos)

Send a `GET` request to the `/{user-id}/videos?type=uploaded` to get all videos a person has uploaded or  `/{user-id}/videos?type=tagged` to get all videos a person has been tagged in.

```curl
curl -i -X GET "https://graph.facebook.com/{user-id}/videos
  ?type=uploaded
  &access_token={user-access-token}"
```

On success, your app receives the following response:

```json
{
  "data": [
    {
      "description": "Rain",
      "updated_time": "2020-05-18T20:07:47+0000",
      "id": "{video-id-1}"
    },
    {
      "updated_time": "2020-05-20T12:26:19+0000",
      "id": "{video-id-2}"
    },
    ...
  ]
  "paging": {
    "cursors": {
      "before": "...",
      "after": "..."
    }
  }
}
```

#### Limitations

* By default, a `GET` request without a `type` specified will return videos a person was tagged in.
* If no `description` is returned, the video post contained no accompanying text.

## See Also

* [Facebook Permission Reference](https://developers.facebook.com/docs/permissions/reference)
* [Facebook Feature Permission Reference](https://developers.facebook.com/docs/apps/review/feature)
* [Page Video Reference](https://developers.facebook.com/docs/graph-api/reference/page/videos)
* [User Video Reference](https://developers.facebook.com/docs/graph-api/reference/user/videos)
* [Video Reference](https://developers.facebook.com/docs/graph-api/reference/video)
