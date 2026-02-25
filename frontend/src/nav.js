export var nav = {
  library: function () {
    window.location.hash = '#/';
  },
  article: function (id) {
    window.location.hash = '#/article/' + id;
  },
  articleMarkdown: function (id) {
    window.location.hash = '#/article/' + id + '/markdown';
  },
  search: function () {
    window.location.hash = '#/search';
  },
  tags: function () {
    window.location.hash = '#/tags';
  },
  tagFilter: function (tagId) {
    window.location.hash = '#/?tag=' + encodeURIComponent(tagId);
  },
  login: function () {
    window.location.hash = '#/login';
  },
};
